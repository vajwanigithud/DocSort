"""
Developer helper to inspect OCR-based invoice filename suggestions.

Usage:
  python -m docsort.tools.invoice_suggest_cli <path-to-pdf-or-image> [--pages 2]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from docsort.app.services import ocr_suggestion_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview OCR filename suggestions for a file.")
    parser.add_argument("path", type=Path, help="PDF or image file")
    parser.add_argument("--pages", type=int, default=2, help="Max pages to OCR (default: 2)")
    args = parser.parse_args()
    target = args.path
    if not target.exists():
        print(f"Missing file: {target}")
        return 1
    text = ocr_suggestion_service.get_text_for_pdf(str(target), max_pages=max(1, int(args.pages or 1)))
    fallback_stem = target.stem
    suggestions = ocr_suggestion_service.build_ocr_suggestions(text, fallback_stem)
    print(f"Suggestions for {target}:")
    for idx, name in enumerate(suggestions, start=1):
        print(f"{idx}. {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
