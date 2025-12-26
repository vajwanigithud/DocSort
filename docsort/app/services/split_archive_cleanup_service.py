from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from PyPDF2 import PdfReader

BASE_STEM_RE = re.compile(r"^(?P<base>.+?)_\d{14}_[0-9a-fA-F]{8}$")
RANGE_RE = re.compile(r"_p(?P<start>\d+)-(?P<end>\d+)\b", re.IGNORECASE)


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


def run_cleanup(source_root: Path, apply: bool) -> str:
    start_ts = datetime.now()
    lines: List[str] = [
        f"Split archive cleanup started {start_ts.isoformat()} (apply={'yes' if apply else 'no'})",
        f"Source root: {source_root}",
    ]
    archive_dir = source_root / "_split_archive"
    if not archive_dir.exists():
        lines.append(f"Archive folder does not exist: {archive_dir}")
        lines.append("No eligible archived originals found.")
        lines.append(f"Finished {datetime.now().isoformat()}")
        return "\n".join(lines)

    candidates = sorted(archive_dir.glob("*.pdf"))
    lines.append(f"Found {len(candidates)} archived originals.")
    if not candidates:
        lines.append("No eligible archived originals found.")
        lines.append(f"Finished {datetime.now().isoformat()}")
        return "\n".join(lines)

    would_delete: List[str] = []
    deleted: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    for archived in candidates:
        try:
            safe, reason, outputs, total_pages, overlap = _assess_candidate(archived, source_root)
            overlap_note = " (overlap detected)" if overlap else ""
            if not safe:
                skipped.append(f"{archived.name} -> {reason}")
                continue
            desc = f"{archived.name} ({total_pages or '?'}p) via {len(outputs)} splits{overlap_note}"
            if apply:
                try:
                    archived.unlink()
                    deleted.append(desc)
                except PermissionError as exc:
                    errors.append(f"{archived.name} permission denied ({exc})")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{archived.name} delete failed ({exc})")
            else:
                would_delete.append(desc)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{archived.name} unexpected error ({exc})")

    lines.append("")
    if apply and deleted:
        lines.append("Deleted:")
        lines.extend(f"  - {msg}" for msg in deleted)
    elif not apply and would_delete:
        lines.append("Would delete:")
        lines.extend(f"  - {msg}" for msg in would_delete)
    else:
        lines.append("No eligible archived originals found.")

    if skipped:
        lines.append("")
        lines.append("Skipped (reason):")
        lines.extend(f"  - {msg}" for msg in skipped)

    if errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"  - {msg}" for msg in errors)

    lines.append("")
    end_ts = datetime.now()
    lines.append(
        f"Summary: candidates={len(candidates)} "
        f"{'deleted' if apply else 'would_delete'}={len(deleted) if apply else len(would_delete)} "
        f"skipped={len(skipped)} errors={len(errors)}"
    )
    lines.append(f"Finished {end_ts.isoformat()}")
    return "\n".join(lines)

