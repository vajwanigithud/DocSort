from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Dict, Tuple

from docsort.app.storage.settings_store import FolderConfig, get_folder_config

ResolvedConfig = Dict[str, Path]


def resolve_paths(cfg: FolderConfig) -> ResolvedConfig:
    resolved: ResolvedConfig = {}
    for role, raw in {
        "staging": cfg.staging,
        "splitter": cfg.splitter,
        "rename": cfg.rename,
        "destination": cfg.destination,
    }.items():
        if not raw:
            continue
        try:
            resolved[role] = Path(raw).expanduser().resolve()
        except Exception:
            resolved[role] = Path(raw)
    return resolved


def validate_folder_config(cfg: FolderConfig | None = None) -> Tuple[bool, str, ResolvedConfig]:
    if cfg is None:
        cfg = get_folder_config()
    resolved = resolve_paths(cfg)
    required = ["staging", "splitter", "rename", "destination"]
    missing = [r for r in required if r not in resolved]
    if missing:
        return False, f"Set folders for: {', '.join(missing)}", resolved

    for (role_a, path_a), (role_b, path_b) in combinations(resolved.items(), 2):
        if path_a == path_b:
            return False, f"{role_a.title()} and {role_b.title()} cannot be the same folder", resolved
        try:
            path_b.relative_to(path_a)
            return False, f"{role_b.title()} cannot be inside {role_a.title()}", resolved
        except Exception:
            pass
        try:
            path_a.relative_to(path_b)
            return False, f"{role_a.title()} cannot be inside {role_b.title()}", resolved
        except Exception:
            pass

    return True, "", resolved


def run_self_test(cfg: FolderConfig | None = None) -> Tuple[bool, str, ResolvedConfig]:
    ok, msg, resolved = validate_folder_config(cfg)
    printable = {k: Path(v).as_posix() if isinstance(v, Path) else str(v) for k, v in resolved.items()}
    return ok, msg, printable
