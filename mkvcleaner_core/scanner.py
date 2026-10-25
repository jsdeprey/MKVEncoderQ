from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from typing import Iterator

from .models import MediaFile


IGNORED_MARKERS = (".cleaning.", ".mkvclean-temp.", ".video-optimizer-")


def _eligible_name(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() == ".mkv" and not any(
        marker in name for marker in IGNORED_MARKERS
    )


def _eligible(path: Path) -> bool:
    return path.is_file() and _eligible_name(path)


def scan_iter(
    path: Path,
    recursive: bool,
    run_event: Event | None = None,
    stop_event: Event | None = None,
) -> Iterator[MediaFile]:
    path = path.expanduser().resolve()

    if path.is_file():
        if _eligible(path):
            yield MediaFile(path)
        return

    if not path.is_dir():
        raise FileNotFoundError(f"Path does not exist: {path}")

    pending = [path]
    while pending:
        if stop_event is not None and stop_event.is_set():
            return
        if run_event is not None:
            run_event.wait()
        if stop_event is not None and stop_event.is_set():
            return

        current = pending.pop()
        directories: list[Path] = []
        files: list[Path] = []
        with os.scandir(current) as entries:
            for entry in entries:
                if stop_event is not None and stop_event.is_set():
                    return
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if recursive:
                            directories.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        candidate = Path(entry.path)
                        if _eligible_name(candidate):
                            files.append(candidate)
                except OSError:
                    continue

        for candidate in sorted(files, key=lambda item: item.name.lower()):
            yield MediaFile(candidate)
        pending.extend(
            sorted(directories, key=lambda item: item.name.lower(), reverse=True)
        )


def scan_path(path: Path, recursive: bool) -> list[MediaFile]:
    return list(scan_iter(path, recursive))
