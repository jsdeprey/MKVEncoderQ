from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import application_directory


def resolve_program(configured: str) -> str | None:
    """Find a helper beside the app, at its configured path, or on PATH."""
    name = Path(configured).name
    local_names = [name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        local_names.insert(0, f"{name}.exe")
    for local_name in local_names:
        candidate = application_directory() / local_name
        if candidate.is_file():
            return str(candidate)

    configured_path = Path(configured).expanduser()
    if configured_path.is_file():
        return str(configured_path.resolve())
    found = shutil.which(configured)
    return str(Path(found).resolve()) if found else None
