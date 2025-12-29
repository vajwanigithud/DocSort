from pathlib import Path
from typing import List, Optional


DEFAULT_ROOT = Path("./_destinations").resolve()


class FolderService:
    def __init__(self, root: Optional[str] = None) -> None:
        self._root: Path = DEFAULT_ROOT
        self._configured: bool = False
        if root:
            self.set_root(root)
        else:
            self._configured = False

    @property
    def root(self) -> Path:
        return self._root

    @property
    def is_configured(self) -> bool:
        return self._configured

    def set_root(self, path: str) -> Path:
        self._root = Path(path).resolve()
        self._configured = True
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def clear_root(self) -> None:
        self._configured = False

    def list_folders(self) -> List[str]:
        if not self._configured:
            return []
        root = self._root
        root.mkdir(parents=True, exist_ok=True)
        return sorted(
            [p.name for p in root.iterdir() if p.is_dir()]
        )

    def create_folder(self, name: str) -> str:
        if not self._configured:
            raise ValueError("Destination root not configured")
        sanitized = name.replace(" ", "_")
        root = self._root
        root.mkdir(parents=True, exist_ok=True)
        new_path = root / sanitized
        new_path.mkdir(parents=True, exist_ok=True)
        return str(new_path)


folder_service = FolderService()
