"""
CLI helper to validate the configured 4-folder workflow.

Usage:
  python -m docsort.tools.folder_self_test
"""

from __future__ import annotations

from typing import Iterable

from docsort.app.storage import settings_store
from docsort.app.utils import folder_validation


def _format_paths(items: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"- {name}: {path}" for name, path in items)


def main() -> int:
    cfg = settings_store.get_folder_config()
    ok, msg, resolved = folder_validation.run_self_test(cfg)
    printable = {k: str(v) for k, v in resolved.items()}
    if ok:
        print("Folder config OK.")
        print(_format_paths(printable.items()))
        return 0
    print(f"Folder config INVALID: {msg}")
    if printable:
        print(_format_paths(printable.items()))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
