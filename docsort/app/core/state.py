import logging
import queue
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class DocumentItem:
    id: str
    source_path: str
    display_name: str
    page_count: int
    notes: str
    suggested_folder: str
    suggested_name: str
    confidence: float
    vendor: str
    doctype: str
    number: str
    date_str: str
    route_hint: str = "AUTO"
    is_virtual: bool = False
    parent_id: Optional[str] = None
    split_group: Optional[str] = None


def _path_key(path: str) -> str:
    from pathlib import Path

    return str(Path(path).resolve())


class AppState:
    def __init__(self) -> None:
        self.scanned_items: List[DocumentItem] = []
        self.splitter_items: List[DocumentItem] = []
        self.rename_items: List[DocumentItem] = []
        self.attention_items: List[DocumentItem] = []
        self.done_items: List[DocumentItem] = []
        self.pending_scanned_paths: "queue.Queue[str]" = queue.Queue()
        self.pending_attention_messages: "queue.Queue[dict]" = queue.Queue()
        self.log = logging.getLogger(__name__)

    def _find_and_remove(self, collection: List[DocumentItem], item_id: str) -> Optional[DocumentItem]:
        for idx, item in enumerate(collection):
            if item.id == item_id:
                return collection.pop(idx)
        return None

    def move_item(self, source: List[DocumentItem], target: List[DocumentItem], item_id: str) -> Optional[DocumentItem]:
        item = self._find_and_remove(source, item_id)
        if item:
            target.append(item)
        return item

    def move_between_named_lists(self, source_name: str, target_name: str, item_id: str) -> Optional[DocumentItem]:
        source_list: List[DocumentItem] = getattr(self, source_name, [])
        target_list: List[DocumentItem] = getattr(self, target_name, [])
        return self.move_item(source_list, target_list, item_id)

    def request_add_scanned_item(self, item: DocumentItem) -> None:
        # Deprecated; use enqueue_scanned_path instead.
        self.enqueue_scanned_path(item.source_path)

    def enqueue_scanned_path(self, path: str) -> None:
        self.pending_scanned_paths.put(path)

    def enqueue_attention(self, item_id: Optional[str], source_path: str, error: str) -> None:
        self.pending_attention_messages.put({"item_id": item_id, "source_path": source_path, "error": error})

    # ----------------------------------------------------------------------
    # Hydration helpers
    # ----------------------------------------------------------------------
    def _scan_pdfs(self, root: Path) -> Dict[str, Path]:
        results: Dict[str, Path] = {}
        if not root.exists():
            return results
        try:
            root_resolved = root.resolve()
        except Exception:
            root_resolved = root
        for path in root_resolved.rglob("*.pdf"):
            if not path.is_file():
                continue
            try:
                rel_parts = path.resolve().relative_to(root_resolved).parts
            except Exception:
                continue
            # skip underscore folders anywhere under root
            if any(part.startswith("_") for part in rel_parts[:-1]):
                continue
            key = str(path.resolve())
            results[key] = path.resolve()
        return results

    def hydrate_from_folder(self, list_name: str, root: Path, route_hint: str = "AUTO") -> None:
        existing: List[DocumentItem] = getattr(self, list_name, [])
        existing_by_path = {_path_key(item.source_path): item for item in existing}
        scanned = self._scan_pdfs(root)

        new_items: List[DocumentItem] = []
        for resolved_str, path in scanned.items():
            if resolved_str in existing_by_path:
                item = existing_by_path[resolved_str]
                item.source_path = resolved_str
                item.display_name = path.name
                new_items.append(item)
            else:
                new_items.append(
                    DocumentItem(
                        id=uuid.uuid4().hex,
                        source_path=resolved_str,
                        display_name=path.name,
                        page_count=1,
                        notes="",
                        suggested_folder="",
                        suggested_name="",
                        confidence=0.0,
                        vendor="Vendor",
                        doctype="Type",
                        number="000",
                        date_str="00-00-0000",
                        route_hint=route_hint,
                    )
                )

        setattr(self, list_name, new_items)
        self.log.info("Hydrated %s from %s count=%s", list_name, root, len(new_items))
