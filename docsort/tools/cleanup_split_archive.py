"""
Safely remove archived originals after verifying split outputs cover all pages.

Run:
  python -m docsort.tools.cleanup_split_archive          # dry-run
  python -m docsort.tools.cleanup_split_archive --apply  # delete eligible files
"""
import argparse
import logging
import sys
from pathlib import Path

from docsort.app.services import split_archive_cleanup_service
from docsort.app.storage import settings_store


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


def run_cleanup_split_archive(dry_run: bool = True, verbose: bool = False) -> int:
    _setup_logging(verbose)
    source_root_str = settings_store.get_source_root()
    if not source_root_str:
        logging.error("No source_root configured; set it in settings before running cleanup.")
        return 1

    source_root = Path(source_root_str)
    if not source_root.exists():
        logging.error("Source root does not exist: %s", source_root)
        return 1
    if not source_root.is_dir():
        logging.error("Source root is not a directory: %s", source_root)
        return 1

    report = split_archive_cleanup_service.run_cleanup(source_root, apply=not dry_run)
    print(report)
    return 0


def main() -> None:
    args = _parse_args()
    exit_code = run_cleanup_split_archive(dry_run=not args.apply, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
