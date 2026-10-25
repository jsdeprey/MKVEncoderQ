from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import CleanerConfig
from .metadata import inspect_for_cleaning
from .models import MediaFile


def _track_signature(media: MediaFile) -> list[tuple]:
    return [
        (
            track.type,
            track.codec,
            track.codec_id,
            track.language,
            track.default_track,
            track.forced_track,
            track.enabled_track,
        )
        for track in media.tracks
    ]


def verify_output(
    original: MediaFile,
    mkvmerge: Path,
    config: CleanerConfig,
) -> None:
    if not original.temp_path:
        raise RuntimeError("No temporary output is available for verification")

    cleaned = inspect_for_cleaning(
        MediaFile(original.temp_path),
        mkvmerge,
        config,
    )

    if len(original.tracks) != len(cleaned.tracks):
        raise RuntimeError("Verification failed: track count changed")

    if _track_signature(original) != _track_signature(cleaned):
        raise RuntimeError("Verification failed: track metadata changed unexpectedly")

    if not config.remove_chapters:
        if original.chapters_present != cleaned.chapters_present:
            raise RuntimeError("Verification failed: chapters changed unexpectedly")

    if not config.remove_attachments:
        if original.attachments_present != cleaned.attachments_present:
            raise RuntimeError("Verification failed: attachments changed unexpectedly")

    original.verified = True


def _next_backup_path(path: Path) -> Path:
    candidate = path.with_suffix(path.suffix + ".bak")
    index = 1
    while candidate.exists():
        candidate = path.with_suffix(path.suffix + f".bak.{index}")
        index += 1
    return candidate


def replace_original(media: MediaFile, config: CleanerConfig) -> None:
    if not media.temp_path or not media.temp_path.exists():
        raise RuntimeError("Temporary output is missing")

    stat = media.path.stat()
    backup_path: Path | None = None

    if config.keep_backup:
        backup_path = _next_backup_path(media.path)
        shutil.copy2(media.path, backup_path)
        media.backup_path = backup_path

    os.replace(media.temp_path, media.path)

    if config.preserve_timestamps:
        os.utime(media.path, (stat.st_atime, stat.st_mtime))

    media.cleaned_size = media.path.stat().st_size
