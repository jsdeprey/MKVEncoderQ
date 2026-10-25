from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import application_directory
from .models import MediaInfo
from .probe import probe


@dataclass(frozen=True)
class MediaInspection:
    path: Path
    size: int
    modified_ns: int
    codec: str
    width: int
    height: int
    bit_rate: int | None
    duration: float | None
    rating: str

    @property
    def resolution(self) -> str:
        if self.width <= 0 or self.height <= 0:
            return "Unknown"
        return f"{self.width}×{self.height}"

    @property
    def resolution_group(self) -> str:
        if self.width >= 3000 or self.height >= 1400:
            return "2160p / 4K"
        if self.width >= 1600 or self.height >= 800:
            return "1080p"
        if self.width >= 1100 or self.height >= 600:
            return "720p"
        return "SD"

    @property
    def codec_label(self) -> str:
        return {
            "h264": "H.264", "hevc": "H.265", "h265": "H.265",
            "av1": "AV1", "mpeg2video": "MPEG-2", "mpeg4": "MPEG-4",
            "vp9": "VP9", "vp8": "VP8",
        }.get(self.codec.lower(), self.codec.upper() if self.codec else "Unknown")

    @property
    def media_text(self) -> str:
        return f"{self.codec_label} • {self.resolution_group}"


def h264_waste_rating(codec: str, width: int, height: int,
                      bit_rate: int | None) -> str:
    """Apply the user's established NAS H.264 bitrate-density thresholds."""
    if codec.lower() != "h264":
        return "—"
    if width <= 0 or height <= 0 or not bit_rate or bit_rate <= 0:
        return "UNKNOWN"
    density = bit_rate / (width * height)
    if ((width >= 3840 and density > 0.25)
            or (width >= 1920 and density > 0.20)
            or (width >= 1280 and density > 0.15)):
        return "WASTEFUL"
    return "OK"


def h265_size_review_rating(codec: str, width: int, height: int,
                            size: int, duration: float | None) -> str:
    """Flag unusually large HEVC files without calling them wasteful."""
    if codec.lower() not in {"hevc", "h265"}:
        return "—"
    if width <= 0 or height <= 0 or size <= 0 or not duration or duration <= 0:
        return "UNKNOWN"
    if width >= 3000 or height >= 1400:
        two_hour_limit_gib = 15.0
    elif width >= 1600 or height >= 800:
        two_hour_limit_gib = 5.0
    elif width >= 1100 or height >= 600:
        two_hour_limit_gib = 3.0
    else:
        two_hour_limit_gib = 1.5
    normalized_two_hour_size = size * (7200.0 / duration)
    return ("REVIEW SIZE" if normalized_two_hour_size >
            two_hour_limit_gib * 1024 ** 3 else "OK")


def media_rating(codec: str, width: int, height: int, bit_rate: int | None,
                 size: int, duration: float | None) -> str:
    if codec.lower() == "h264":
        return h264_waste_rating(codec, width, height, bit_rate)
    if codec.lower() in {"hevc", "h265"}:
        return h265_size_review_rating(codec, width, height, size, duration)
    return "—"


def inspection_from_media(media: MediaInfo, modified_ns: int | None = None) -> MediaInspection:
    video = media.videos[0] if media.videos else None
    codec = video.codec_name if video else "unknown"
    width = video.width if video else 0
    height = video.height if video else 0
    bit_rate = (video.bit_rate if video and video.bit_rate else media.bit_rate)
    if modified_ns is None:
        try:
            modified_ns = media.path.stat().st_mtime_ns
        except OSError:
            modified_ns = 0
    return MediaInspection(
        path=media.path.resolve(), size=media.size, modified_ns=modified_ns,
        codec=codec, width=width, height=height, bit_rate=bit_rate,
        duration=media.duration,
        rating=media_rating(codec, width, height, bit_rate, media.size,
                            media.duration),
    )


def inspect_media(path: Path, ffprobe: str, timeout: float = 45.0) -> MediaInspection:
    path = path.resolve()
    stat = path.stat()
    return inspection_from_media(
        probe(path, ffprobe, timeout=timeout), stat.st_mtime_ns)


def inspection_matches(result: MediaInspection, codec_filter: str,
                       resolution_filter: str, rating_filter: str) -> bool:
    codec = result.codec.lower()
    if codec_filter == "H.264" and codec != "h264":
        return False
    if codec_filter == "H.265 / HEVC" and codec not in {"hevc", "h265"}:
        return False
    if codec_filter == "AV1" and codec != "av1":
        return False
    if codec_filter == "Other" and codec in {"h264", "hevc", "h265", "av1"}:
        return False
    group = result.resolution_group
    if resolution_filter == "1080p or Lower" and group == "2160p / 4K":
        return False
    if resolution_filter not in {"All", "1080p or Lower"} and group != resolution_filter:
        return False
    if rating_filter != "All" and result.rating != rating_filter.upper():
        return False
    return True


def inspection_sort_key(result: MediaInspection | None, sort_mode: str,
                        filename: str) -> tuple:
    if result is None:
        return (1, filename.casefold())
    if sort_mode == "Smallest First":
        return (0, result.size, filename.casefold())
    if sort_mode == "Most Wasteful":
        rating = {"WASTEFUL": 0, "UNKNOWN": 1, "OK": 2, "—": 3}.get(
            result.rating, 4)
        codec = 0 if result.codec.lower() == "h264" else 1
        return (0, codec, rating, -result.size, filename.casefold())
    if sort_mode == "Filename":
        return (0, filename.casefold())
    return (0, -result.size, filename.casefold())


class InspectionCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (application_directory() / "media_inspection_cache.json")
        self._lock = threading.RLock()
        self._items: dict[str, MediaInspection] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                return
            for key, values in raw.items():
                try:
                    values = dict(values)
                    values["path"] = Path(values["path"])
                    self._items[key] = MediaInspection(**values)
                except (KeyError, TypeError, ValueError):
                    continue

    def get(self, path: Path) -> MediaInspection | None:
        path = path.resolve()
        with self._lock:
            result = self._items.get(str(path))
        if result is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        if result.size != stat.st_size or result.modified_ns != stat.st_mtime_ns:
            return None
        return result

    def put(self, result: MediaInspection, persist: bool = True) -> None:
        with self._lock:
            self._items[str(result.path.resolve())] = result
            if persist:
                self.save()

    def rename(self, old: Path, new: Path) -> None:
        old, new = old.resolve(), new.resolve()
        with self._lock:
            result = self._items.pop(str(old), None)
            if result is None:
                return
            try:
                stat = new.stat()
            except OSError:
                self.save()
                return
            self._items[str(new)] = MediaInspection(
                path=new, size=stat.st_size, modified_ns=stat.st_mtime_ns,
                codec=result.codec, width=result.width, height=result.height,
                bit_rate=result.bit_rate, duration=result.duration,
                rating=result.rating)
            self.save()

    def save(self) -> None:
        with self._lock:
            payload = {
                key: {**asdict(value), "path": str(value.path)}
                for key, value in self._items.items()
            }
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
