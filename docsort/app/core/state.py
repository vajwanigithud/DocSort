import queue
import threading
from dataclasses import dataclass
from typing import List, Optional


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
        self._seed_dummy_data()

    def _seed_dummy_data(self) -> None:
        self.scanned_items = [
            DocumentItem(
                id="scan-001",
                source_path="C:/scans/scan_001.pdf",
                display_name="Scan 001",
                page_count=1,
                notes="",
                suggested_folder="invoices",
                suggested_name="invoice_001",
                confidence=0.82,
                vendor="VendorA",
                doctype="Invoice",
                number="001",
                date_str="01-01-2025",
                route_hint="AUTO",
            ),
            DocumentItem(
                id="scan-002",
                source_path="C:/scans/scan_002.pdf",
                display_name="Scan 002",
                page_count=10,
                notes="multi-page",
                suggested_folder="statements",
                suggested_name="statement_july",
                confidence=0.77,
                vendor="VendorB",
                doctype="Statement",
                number="002",
                date_str="05-01-2025",
                route_hint="AUTO",
            ),
        ]
        self.splitter_items = [
            DocumentItem(
                id="split-001",
                source_path="C:/scans/split_001.pdf",
                display_name="Splitter Candidate 1",
                page_count=10,
                notes="needs split",
                suggested_folder="",
                suggested_name="",
                confidence=0.5,
                vendor="VendorC",
                doctype="Packet",
                number="SPL-001",
                date_str="10-01-2025",
                route_hint="AUTO",
            )
        ]
        self.rename_items = [
            DocumentItem(
                id="rename-001",
                source_path="C:/scans/rename_001.pdf",
                display_name="Rename 1",
                page_count=4,
                notes="",
                suggested_folder="receipts",
                suggested_name="receipt_aug",
                confidence=0.74,
                vendor="VendorD",
                doctype="Receipt",
                number="REN-001",
                date_str="15-01-2025",
                route_hint="RENAME",
            ),
            DocumentItem(
                id="rename-002",
                source_path="C:/scans/rename_002.pdf",
                display_name="Rename 2",
                page_count=2,
                notes="",
                suggested_folder="letters",
                suggested_name="letter_client",
                confidence=0.69,
                vendor="VendorE",
                doctype="Letter",
                number="REN-002",
                date_str="20-01-2025",
                route_hint="RENAME",
            ),
        ]
        self.attention_items = [
            DocumentItem(
                id="attn-001",
                source_path="C:/scans/attn_001.pdf",
                display_name="Needs Attention 1",
                page_count=3,
                notes="missing page?",
                suggested_folder="",
                suggested_name="",
                confidence=0.4,
                vendor="VendorF",
                doctype="Other",
                number="ATTN-001",
                date_str="25-01-2025",
                route_hint="AUTO",
            )
        ]
        self.done_items = []

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
