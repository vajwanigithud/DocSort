from pathlib import Path
from typing import List, Optional


DEFAULT_ROOT = Path("./_destinations").resolve()


class FolderService:
    def __init__(self, root: Optional[str] = None) -> None:
        self._root: Path = DEFAULT_ROOT
        self._configured: bool = False
        if root:
            self.set_root(root)

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

    def list_folders(self) -> List[str]:
        root = self._root
        root.mkdir(parents=True, exist_ok=True)
        return sorted(
            [p.name for p in root.iterdir() if p.is_dir()]
        )

    def create_folder(self, name: str) -> str:
        sanitized = name.replace(" ", "_")
        root = self._root
        root.mkdir(parents=True, exist_ok=True)
        new_path = root / sanitized
        new_path.mkdir(parents=True, exist_ok=True)
        return str(new_path)


folder_service = FolderService()
