import os
import shutil
from pathlib import Path

from docsort.app.services import naming_service


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def unique_path(dest_dir: str, filename: str) -> str:
    dest_dir_path = Path(dest_dir)
    ensure_dir(dest_dir)
    sanitized = naming_service.enforce_no_spaces(filename)
    target = dest_dir_path / sanitized
    stem = target.stem
    suffix = target.suffix or ".pdf"
    counter = 1
    while target.exists():
        target = dest_dir_path / f"{stem}_{counter}{suffix}"
        counter += 1
    return str(target)


def move_file_safe(src: str, dest_dir: str, filename: str) -> dict:
    try:
        src_path = Path(src)
        dest_dir_path = Path(dest_dir)
        ensure_dir(str(dest_dir_path))
        final_dest = Path(unique_path(str(dest_dir_path), filename))

        # Same drive check
        if src_path.drive == final_dest.drive:
            os.replace(src_path, final_dest)
        else:
            shutil.copy2(src_path, final_dest)
            # verify copy size
            if src_path.stat().st_size != final_dest.stat().st_size:
                final_dest.unlink(missing_ok=True)
                return {"ok": False, "src": str(src_path), "dest": str(final_dest), "error": "size mismatch on copy"}
            src_path.unlink(missing_ok=True)

        return {"ok": True, "src": str(src_path), "dest": str(final_dest)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "src": src, "dest": str(Path(dest_dir) / filename), "error": str(exc)}
