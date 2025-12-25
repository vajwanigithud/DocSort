from pathlib import Path
from typing import Iterable, List, Tuple

from docsort.app.core.state import DocumentItem

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def route_item(item: DocumentItem) -> str:
    ext = Path(item.source_path).suffix.lower()
    hint = (item.route_hint or "AUTO").upper()
    if hint == "SPLIT":
        return "splitter"
    if hint == "RENAME":
        return "rename"
    # AUTO
    if ext in IMAGE_EXT:
        return "rename"
    if ext == ".pdf":
        return "rename"
    return "rename"


def route_items(items: Iterable[DocumentItem]) -> List[Tuple[DocumentItem, str]]:
    results: List[Tuple[DocumentItem, str]] = []
    for item in items:
        results.append((item, route_item(item)))
    return results
