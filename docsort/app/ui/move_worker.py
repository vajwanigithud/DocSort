import os
import shutil
from pathlib import Path

from PySide6 import QtCore


class MoveWorker(QtCore.QObject):
    finished = QtCore.Signal(bool, str, str, str, str, str, str, str)  # success, msg, src, dest, doc_id, final_folder, final_name, status

    def __init__(self, src: str, dest: str, same_drive: bool, final_folder: str, final_name: str, doc_id: str) -> None:
        super().__init__()
        self.src = src
        self.dest = dest
        self.same_drive = same_drive
        self.final_folder = final_folder
        self.final_name = final_name
        self.doc_id = doc_id

    @QtCore.Slot()
    def run(self) -> None:
        try:
            dest_path = Path(self.dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            status = "DONE"
            try:
                if self.same_drive:
                    os.replace(self.src, self.dest)
                else:
                    shutil.move(self.src, self.dest)
            except PermissionError as exc:
                if "WinError 32" in str(exc):
                    # immediate copy fallback
                    shutil.copy2(self.src, self.dest)
                    status = "PENDING_DELETE"
                else:
                    raise
            # verify copy/move
            if not Path(self.dest).exists():
                raise IOError("Destination missing after move/copy")
            self.finished.emit(True, "ok", self.src, self.dest, self.doc_id, self.final_folder, self.final_name, status)
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(False, str(exc), self.src, self.dest, self.doc_id, self.final_folder, self.final_name, "ERROR")
