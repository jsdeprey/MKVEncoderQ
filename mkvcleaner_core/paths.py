from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def locate_mkvmerge(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []

    if explicit:
        selected = Path(explicit)
        candidates.append(selected / "mkvmerge.exe" if selected.is_dir() else selected)

    app_dir = application_directory()
    candidates.extend([
        app_dir / "mkvmerge.exe",
        app_dir / "MKVToolNix" / "mkvmerge.exe",
    ])

    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "MKVToolNix" / "mkvmerge.exe")

    found = shutil.which("mkvmerge") or shutil.which("mkvmerge.exe")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        "mkvmerge.exe was not found. Install MKVToolNix, place mkvmerge.exe "
        "beside MKVCleaner.exe, or use --mkvmerge."
    )


def locate_mkvpropedit(mkvmerge: Path) -> Path:
    """Locate mkvpropedit from the same MKVToolNix installation as mkvmerge."""
    names = ("mkvpropedit.exe", "mkvpropedit")
    candidates = [mkvmerge.parent / name for name in names]

    app_dir = application_directory()
    candidates.extend(app_dir / name for name in names)
    candidates.extend(app_dir / "MKVToolNix" / name for name in names)

    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if base:
            candidates.extend(Path(base) / "MKVToolNix" / name for name in names)

    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        "mkvpropedit.exe was not found in the MKVToolNix installation. "
        "Reinstall MKVToolNix or place mkvpropedit.exe beside MKVCleaner.exe."
    )
