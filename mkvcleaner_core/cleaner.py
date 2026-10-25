from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .config import CleanerConfig
from .metadata import (default_subtitles_to_disable,
                       forced_subtitles_to_disable, inspect_for_cleaning)
from .models import MediaFile
from .paths import locate_mkvpropedit
from .process import run_tool

PhaseCallback = Callable[[str], None]


def build_clean_command(media: MediaFile, mkvmerge: Path, config: CleanerConfig) -> list[str]:
    """Build the mkvmerge fallback command."""
    temp_name = f"{media.path.stem}.mkvclean-temp.{uuid4().hex}.mkv"
    media.temp_path = media.path.with_name(temp_name)
    command = [str(mkvmerge), "--output", str(media.temp_path)]

    if config.remove_title and media.segment_title:
        command.extend(["--title", ""])
    if any((config.remove_video_track_names, config.remove_audio_track_names,
            config.remove_subtitle_track_names)):
        for track in media.tracks:
            if track.name and config.remove_name_for_track(track.type):
                command.extend(["--track-name", f"{track.id}:"])
    if config.remove_track_tags and media.has_track_tags:
        command.append("--no-track-tags")
    if config.remove_global_tags and media.has_global_tags:
        command.append("--no-global-tags")
    if config.remove_chapters and media.chapters_present:
        command.append("--no-chapters")
    if config.remove_attachments and media.attachments_present:
        command.append("--no-attachments")

    for track in default_subtitles_to_disable(media, config):
        command.extend(["--default-track-flag", f"{track.id}:0"])
    for track in forced_subtitles_to_disable(media, config):
        command.extend(["--forced-display-flag", f"{track.id}:0"])

    command.append(str(media.path))
    return command


def clean_file(media: MediaFile, mkvmerge: Path, config: CleanerConfig) -> None:
    command = build_clean_command(media, mkvmerge, config)
    result = run_tool(command)
    if result.returncode != 0:
        if media.temp_path and media.temp_path.exists():
            media.temp_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "mkvmerge failed")
    if not media.temp_path or not media.temp_path.exists():
        raise RuntimeError("mkvmerge completed but did not create an output file")
    media.cleaned_size = media.temp_path.stat().st_size


def _next_backup_path(path: Path) -> Path:
    candidate = path.with_suffix(path.suffix + ".bak")
    index = 1
    while candidate.exists():
        candidate = path.with_suffix(path.suffix + f".bak.{index}")
        index += 1
    return candidate


def can_use_mkvpropedit(media: MediaFile, config: CleanerConfig) -> bool:
    """Return whether every requested edit can be done safely in one invocation.

    Removing all tags or global tags is supported directly. Removing only track
    tags while deliberately preserving global tags requires the mkvmerge
    fallback because mkvpropedit needs per-track tag selectors.
    """
    if not media.clean_reasons:
        return False
    track_tags_only_limitation = (
        config.remove_track_tags
        and media.has_track_tags
        and not config.remove_global_tags
        and media.has_global_tags
    )
    return not track_tags_only_limitation


def build_mkvpropedit_command(media: MediaFile, mkvmerge: Path, config: CleanerConfig) -> list[str]:
    mkvpropedit = locate_mkvpropedit(mkvmerge)
    command = [str(mkvpropedit), str(media.path)]

    if config.remove_title and media.segment_title:
        command.extend(["--edit", "info", "--delete", "title"])

    if any((config.remove_video_track_names, config.remove_audio_track_names,
            config.remove_subtitle_track_names)):
        for index, track in enumerate(media.tracks, start=1):
            if track.name and config.remove_name_for_track(track.type):
                command.extend(["--edit", f"track:{index}", "--delete", "name"])

    if config.remove_global_tags and config.remove_track_tags and (media.has_global_tags or media.has_track_tags):
        command.extend(["--tags", "all:"])
    elif config.remove_global_tags and media.has_global_tags:
        command.extend(["--tags", "global:"])

    if config.remove_chapters and media.chapters_present:
        command.extend(["--chapters", ""])

    if config.remove_attachments and media.attachments_present:
        for attachment_id in media.attachment_ids:
            command.extend(["--delete-attachment", str(attachment_id)])

    disable_ids = {track.id for track in default_subtitles_to_disable(media, config)}
    disable_forced_ids = {
        track.id for track in forced_subtitles_to_disable(media, config)}
    for index, track in enumerate(media.tracks, start=1):
        if track.id in disable_ids:
            command.extend(["--edit", f"track:{index}", "--set", "flag-default=0"])
        if track.id in disable_forced_ids:
            command.extend(["--edit", f"track:{index}", "--set", "flag-forced=0"])

    return command


def clean_in_place(
    media: MediaFile,
    mkvmerge: Path,
    config: CleanerConfig,
    phase_callback: PhaseCallback | None = None,
) -> None:
    original_stat = media.path.stat()

    if config.keep_backup:
        if phase_callback:
            phase_callback("Creating backup")
        backup_path = _next_backup_path(media.path)
        shutil.copy2(media.path, backup_path)
        media.backup_path = backup_path

    command = build_mkvpropedit_command(media, mkvmerge, config)
    if len(command) <= 2:
        raise RuntimeError("No mkvpropedit actions were generated")

    if phase_callback:
        phase_callback("Editing metadata")
    result = run_tool(command)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "mkvpropedit failed")

    if config.preserve_timestamps:
        os.utime(media.path, (original_stat.st_atime, original_stat.st_mtime))

    media.cleaned_size = media.path.stat().st_size
    media.cleaning_method = "mkvpropedit"

    if config.verify_output:
        if phase_callback:
            phase_callback("Verifying")
        checked = inspect_for_cleaning(MediaFile(media.path), mkvmerge, config)
        if checked.clean_reasons:
            raise RuntimeError("Verification failed; still present: " + ", ".join(checked.clean_reasons))
        if len(checked.tracks) != len(media.tracks):
            raise RuntimeError("Verification failed: track count changed")
        media.verified = True
