from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Decision(str, Enum):
    RECOMMEND = "RECOMMEND"
    SKIP = "SKIP"
    REVIEW = "REVIEW"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Stream:
    index: int
    codec_type: str
    codec_name: str
    codec_long_name: str = ""
    width: int = 0
    height: int = 0
    bit_rate: int | None = None
    channels: int = 0
    channel_layout: str = ""
    profile: str = ""
    pixel_format: str = ""
    color_primaries: str = ""
    color_space: str = ""
    color_transfer: str = ""
    side_data: tuple[str, ...] = ()
    dolby_vision_profile: int | None = None
    is_default: bool = False
    language: str = ""


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float | None
    bit_rate: int | None
    size: int
    streams: tuple[Stream, ...]
    format_name: str = ""

    @property
    def videos(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.codec_type == "video")

    @property
    def audios(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.codec_type == "audio")

    @property
    def subtitles(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.codec_type == "subtitle")

    @property
    def data_streams(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.codec_type == "data")

    @property
    def attachments(self) -> tuple[Stream, ...]:
        return tuple(s for s in self.streams if s.codec_type == "attachment")

    @property
    def is_hdr(self) -> bool:
        if not self.videos:
            return False
        video = self.videos[0]
        return (video.color_transfer.lower() in {"smpte2084", "arib-std-b67"}
                or "hdr10+" in " ".join(video.side_data).lower()
                or video.dolby_vision_profile is not None)

    @property
    def is_dolby_vision(self) -> bool:
        return bool(self.videos and self.videos[0].dolby_vision_profile is not None)


@dataclass(frozen=True)
class SampleResult:
    number: int
    total: int
    start_seconds: float
    output_size: int
    projected_size: int
    projected_savings_percent: float


@dataclass(frozen=True)
class ScanResult:
    path: Path
    decision: Decision
    reason: str
    media: MediaInfo | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    projected_size: int | None = None
    projected_savings_percent: float | None = None
    samples_tested: int = 0
    sample_results: tuple[SampleResult, ...] = field(default_factory=tuple)
    sampling_elapsed_seconds: float | None = None
