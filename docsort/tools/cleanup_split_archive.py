"""
Safely remove archived originals after verifying split outputs cover all pages.

Run:
  python -m docsort.tools.cleanup_split_archive          # dry-run
  python -m docsort.tools.cleanup_split_archive --apply  # delete eligible files
"""
import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from PyPDF2 import PdfReader

from docsort.app.storage import settings_store

logger = logging.getLogger(__name__)

BASE_STEM_RE = re.compile(r"^(?P<base>.+?)_\d{14}_[0-9a-fA-F]{8}$")
RANGE_RE = re.compile(r"_p(?P<start>\d+)-(?P<end>\d+)\b", re.IGNORECASE)


def _setup_logging(verbose: bool) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s")
    else:
        logging.getLogger().setLevel(logging.DEBUG if verbose else logging.INFO)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cleanup Source/_split_archive by deleting originals whose split outputs fully cover all pages."
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete safe archived originals (default: dry-run).")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    return parser.parse_args()


def _extract_base_stem(path: Path) -> Tuple[str, bool]:
    match = BASE_STEM_RE.match(path.stem)
    if match:
        return match.group("base"), False
    return path.stem, True


def _read_page_count(path: Path) -> Tuple[Optional[int], Optional[str]]:
    try:
        with path.open("rb") as fh:
            reader = PdfReader(fh)
            return len(reader.pages), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _iter_split_outputs(source_root: Path, base_stem: str) -> Iterable[Tuple[Path, int, int]]:
    for child in source_root.iterdir():
        if child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        if child.suffix.lower() != ".pdf":
            continue
        if not child.stem.startswith(base_stem):
            continue
        match = RANGE_RE.search(child.stem)
        if not match:
            continue
        start = int(match.group("start"))
        end = int(match.group("end"))
        yield child, start, end


def _build_coverage(total_pages: int, ranges: List[Tuple[int, int]]) -> Tuple[Set[int], bool]:
    coverage: Set[int] = set()
    overlap = False
    for start, end in ranges:
        for page in range(start, end + 1):
            if page in coverage:
                overlap = True
            coverage.add(page)
    return coverage, overlap


def _assess_candidate(
    archived_path: Path, source_root: Path
) -> Tuple[bool, str, List[Path], Optional[int], Optional[bool]]:
    base_stem, used_fallback = _extract_base_stem(archived_path)
    if used_fallback:
        logger.debug("Using fallback base stem for %s -> %s", archived_path.name, base_stem)

    total_pages, err = _read_page_count(archived_path)
    if err or total_pages is None:
        return False, f"unreadable archived PDF ({err})", [], None, None

    outputs: List[Path] = []
    ranges: List[Tuple[int, int]] = []
    for out_path, start, end in _iter_split_outputs(source_root, base_stem):
        if start < 1 or end < start or end > total_pages:
            return False, f"invalid page range in {out_path.name}", [out_path], total_pages, None
        page_count, out_err = _read_page_count(out_path)
        expected = end - start + 1
        if out_err or page_count is None:
            return False, f"unreadable split output {out_path.name} ({out_err})", [out_path], total_pages, None
        if page_count != expected:
            return False, f"page count mismatch for {out_path.name} expected {expected} got {page_count}", [out_path], total_pages, None
        outputs.append(out_path)
        ranges.append((start, end))

    if not outputs:
        return False, "no matching split outputs in source root", [], total_pages, None

    coverage, overlap = _build_coverage(total_pages, ranges)
    if coverage != set(range(1, total_pages + 1)):
        missing = sorted(set(range(1, total_pages + 1)) - coverage)
        missing_summary = f"missing pages {missing[:5]}..." if len(missing) > 5 else f"missing pages {missing}"
        return False, missing_summary, outputs, total_pages, overlap

    return True, "full coverage", outputs, total_pages, overlap


def run_cleanup_split_archive(dry_run: bool = True, verbose: bool = False) -> int:
    _setup_logging(verbose)
    source_root_str = settings_store.get_source_root()
    if not source_root_str:
        logger.error("No source_root configured; set it in settings before running cleanup.")
        return 1

    source_root = Path(source_root_str)
    if not source_root.exists():
        logger.error("Source root does not exist: %s", source_root)
        return 1
    if not source_root.is_dir():
        logger.error("Source root is not a directory: %s", source_root)
        return 1

    archive_dir = source_root / "_split_archive"
    if not archive_dir.exists():
        logger.info("Archive folder does not exist at %s. It is created after the first split; nothing to clean.", archive_dir)
        return 0

    candidates = sorted(archive_dir.glob("*.pdf"))
    logger.info("Scanning %s archived originals in %s", len(candidates), archive_dir)

    summary: Dict[str, int] = {"candidates": len(candidates), "safe": 0, "deleted": 0, "skipped": 0}
    reasons: Dict[str, int] = {}

    for archived in candidates:
        safe, reason, outputs, total_pages, overlap = _assess_candidate(archived, source_root)
        if overlap:
            logger.warning("Overlapping ranges detected for %s", archived.name)
        if not safe:
            summary["skipped"] += 1
            reasons[reason] = reasons.get(reason, 0) + 1
            logger.info("KEEP %s: %s", archived.name, reason)
            continue

        summary["safe"] += 1
        if dry_run:
            logger.info("DRY-RUN would delete %s (covered %s pages via %s splits)", archived.name, total_pages, len(outputs))
            continue

        try:
            archived.unlink()
            summary["deleted"] += 1
            logger.info("Deleted %s (covered %s pages via %s splits)", archived.name, total_pages, len(outputs))
        except PermissionError as exc:
            summary["skipped"] += 1
            reasons["permission denied"] = reasons.get("permission denied", 0) + 1
            logger.warning("KEEP %s: permission denied (%s)", archived.name, exc)
        except Exception as exc:  # noqa: BLE001
            summary["skipped"] += 1
            msg = f"delete failed: {exc}"
            reasons[msg] = reasons.get(msg, 0) + 1
            logger.warning("KEEP %s: %s", archived.name, msg)

    logger.info(
        "Summary: candidates=%s safe=%s deleted=%s skipped=%s",
        summary["candidates"],
        summary["safe"],
        summary["deleted"],
        summary["skipped"],
    )
    if reasons:
        logger.info("Skip reasons:")
        for reason, count in sorted(reasons.items(), key=lambda item: item[0]):
            logger.info("  %s x%s", reason, count)
    return 0


def main() -> None:
    args = _parse_args()
    exit_code = run_cleanup_split_archive(dry_run=not args.apply, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

