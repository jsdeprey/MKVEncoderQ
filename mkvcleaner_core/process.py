from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence


def run_tool(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run an MKVToolNix command without flashing consoles in GUI mode."""
    kwargs: dict[str, object] = {}
    if os.name == "nt" and os.environ.get("MKVCLEANER_GUI") == "1":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **kwargs,
    )
