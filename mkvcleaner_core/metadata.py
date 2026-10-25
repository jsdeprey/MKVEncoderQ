from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import CleanerConfig
from .models import MediaFile, TrackInfo
from .process import run_tool


def _is_explicit_foreign_language(language: str) -> bool:
    """Return true only for a known, non-English language tag."""
    normalized = str(language or "").strip().lower().replace("_", "-")
    if not normalized or normalized in {"und", "unknown", "zxx"}:
        return False
    return not (normalized in {"en", "eng", "english"}
                or normalized.startswith("en-"))


def _is_english_language(language: str) -> bool:
    normalized = str(language or "").strip().lower().replace("_", "-")
    return (normalized in {"en", "eng", "english"}
            or normalized.startswith("en-"))


def _primary_audio_is_foreign(media: MediaFile) -> bool:
    audio = [track for track in media.tracks
             if track.type.lower() == "audio" and track.enabled_track]
    primary = next((track for track in audio if track.default_track),
                   audio[0] if audio else None)
    return bool(primary is not None
                and _is_explicit_foreign_language(primary.language))


def default_subtitles_to_disable(media: MediaFile, config: CleanerConfig) -> list[TrackInfo]:
    """Return default subtitles unless primary audio is explicitly foreign.

    Empty/unknown language tags follow the normal subtitle-off behavior. A
    subtitle's forced flag is preserved; only its separate default flag is
    cleared.
    """
    if config.clear_all_subtitle_default_flags:
        return [track for track in media.tracks
                if track.type.lower() in {"subtitle", "subtitles"}
                and track.default_track]
    if not config.disable_default_subtitles_for_english_audio:
        return []
    if _primary_audio_is_foreign(media):
        return []
    return [track for track in media.tracks
            if track.type.lower() in {"subtitle", "subtitles"}
            and track.default_track
            and not (track.forced_track
                     and _is_english_language(track.language))]


def forced_subtitles_to_disable(media: MediaFile,
                                config: CleanerConfig) -> list[TrackInfo]:
    """Return explicitly foreign forced subtitles for non-foreign audio."""
    if config.clear_all_subtitle_forced_flags:
        return [track for track in media.tracks
                if track.type.lower() in {"subtitle", "subtitles"}
                and track.forced_track]
    if (not config.disable_default_subtitles_for_english_audio
            or _primary_audio_is_foreign(media)):
        return []
    return [track for track in media.tracks
            if track.type.lower() in {"subtitle", "subtitles"}
            and track.forced_track
            and _is_explicit_foreign_language(track.language)]


def _run_json(mkvmerge: Path, media_path: Path) -> dict[str, Any]:
    try:
        result = run_tool([str(mkvmerge), "-J", str(media_path)])
    except OSError as exc:
        raise RuntimeError(
            f"Unable to start mkvmerge inspection: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "mkvmerge -J failed")
    return json.loads(result.stdout)


def inspect_for_cleaning(
    media: MediaFile,
    mkvmerge: Path,
    config: CleanerConfig,
) -> MediaFile:
    data = _run_json(mkvmerge, media.path)
    media.metadata = data
    media.original_size = media.path.stat().st_size

    container = data.get("container") or {}
    properties = container.get("properties") or {}
    media.segment_title = str(properties.get("title") or "")

    media.tracks.clear()
    media.clean_reasons.clear()

    for raw in data.get("tracks") or []:
        props = raw.get("properties") or {}
        track = TrackInfo(
            id=int(raw.get("id", 0)),
            type=str(raw.get("type") or ""),
            codec=str(raw.get("codec") or ""),
            codec_id=str(props.get("codec_id") or ""),
            language=str(props.get("language") or ""),
            name=str(props.get("track_name") or ""),
            default_track=bool(props.get("default_track", False)),
            forced_track=bool(props.get("forced_track", False)),
            enabled_track=bool(props.get("enabled_track", True)),
            properties=props,
        )
        media.tracks.append(track)

    tags = data.get("global_tags") or data.get("tags") or []
    media.has_global_tags = bool(tags)

    media.has_track_tags = any(
        bool((track.get("properties") or {}).get("tag_track_uid"))
        or bool(track.get("tags"))
        for track in (data.get("tracks") or [])
    )

    chapters = data.get("chapters") or []
    media.chapter_count = len(chapters)
    media.chapters_present = media.chapter_count > 0

    attachments = data.get("attachments") or []
    media.attachment_count = len(attachments)
    media.attachment_ids = [int(item.get("id", index)) for index, item in enumerate(attachments)]
    media.attachments_present = media.attachment_count > 0

    if config.remove_title and media.segment_title:
        media.add_reason("Segment title")
    if any((config.remove_video_track_names, config.remove_audio_track_names,
            config.remove_subtitle_track_names)):
        named_count = sum(1 for track in media.tracks
                          if track.name and config.remove_name_for_track(track.type))
        if named_count:
            media.add_reason(f"Track names ({named_count})")
    if config.remove_global_tags and media.has_global_tags:
        media.add_reason("Global tags")
    if config.remove_track_tags and media.has_track_tags:
        media.add_reason("Track tags")
    if config.remove_chapters and media.chapters_present:
        media.add_reason(f"Chapters ({media.chapter_count})")
    if config.remove_attachments and media.attachments_present:
        media.add_reason(f"Attachments ({media.attachment_count})")
    default_subtitles = default_subtitles_to_disable(media, config)
    if default_subtitles:
        label = ("Subtitle Default flags" if config.clear_all_subtitle_default_flags
                 else "Default subtitles")
        media.add_reason(f"{label} ({len(default_subtitles)})")
    forced_subtitles = forced_subtitles_to_disable(media, config)
    if forced_subtitles:
        label = ("Subtitle Forced flags" if config.clear_all_subtitle_forced_flags
                 else "Forced foreign subtitles")
        media.add_reason(f"{label} ({len(forced_subtitles)})")

    return media
