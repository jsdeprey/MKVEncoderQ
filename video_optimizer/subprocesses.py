from __future__ import annotations

import os
import subprocess
import sys


def is_windowed() -> bool:
    """Return true when running without an attached text console."""
    return os.name == "nt" and (sys.stdout is None or sys.stderr is None)


def helper_creation_flags(*, process_group: bool = False) -> int:
    """Keep helper programs invisible when launched by the GUI build."""
    flags = 0
    if os.name == "nt":
        if is_windowed():
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        if process_group:
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags
