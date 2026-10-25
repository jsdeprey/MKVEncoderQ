from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TrackInfo:
    id: int
    type: str
    codec: str = ""
    codec_id: str = ""
    language: str = ""
    name: str = ""
    default_track: bool = False
    forced_track: bool = False
    enabled_track: bool = True
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MediaFile:
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    tracks: list[TrackInfo] = field(default_factory=list)

    segment_title: str = ""
    has_global_tags: bool = False
    has_track_tags: bool = False
    chapters_present: bool = False
    attachments_present: bool = False
    chapter_count: int = 0
    attachment_count: int = 0
    attachment_ids: list[int] = field(default_factory=list)

    clean_reasons: list[str] = field(default_factory=list)

    verified: bool = False
    temp_path: Path | None = None
    backup_path: Path | None = None
    original_size: int = 0
    cleaned_size: int = 0
    processing_time: float = 0.0
    status: str = "Pending"
    error: str = ""
    cleaning_method: str = ""
    preflight_changes: list[str] = field(default_factory=list)

    @property
    def bytes_saved(self) -> int:
        return max(0, self.original_size - self.cleaned_size)

    def add_reason(self, reason: str) -> None:
        if reason not in self.clean_reasons:
            self.clean_reasons.append(reason)
