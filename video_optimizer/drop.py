from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable


def expand_dropped_paths(
    dropped: Iterable[str | Path], allowed_extensions: set[str]
) -> list[tuple[Path, Path]]:
    """Expand dropped files/folders into unique (file, source-root) pairs."""
    found: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    allowed = {extension.lower() for extension in allowed_extensions}
    for raw in dropped:
        candidate = Path(raw).expanduser().resolve()
        if candidate.is_file():
            entries = ((candidate, candidate.parent),)
        elif candidate.is_dir():
            entries = ((path.resolve(), candidate)
                       for path in candidate.rglob("*") if path.is_file())
        else:
            continue
        for path, source_root in entries:
            if path.suffix.lower() not in allowed or path in seen:
                continue
            seen.add(path)
            found.append((path, source_root))
    return found
