from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import MediaInfo, Stream
from .subprocesses import helper_creation_flags


def _integer(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def probe(path: Path, ffprobe: str = "ffprobe",
          timeout: float | None = None) -> MediaInfo:
    command = [ffprobe, "-v", "error", "-show_format", "-show_streams",
               "-of", "json", str(path)]
    try:
        completed = subprocess.run(command, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", check=False,
                                   creationflags=helper_creation_flags(), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffprobe inspection timed out after {timeout:g} seconds") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to start ffprobe: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip() or "unknown ffprobe error"
        raise RuntimeError(detail)
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON: {exc}") from exc

    streams = []
    for raw in data.get("streams", []):
        raw_side_data = raw.get("side_data_list", [])
        side_data = tuple(str(item.get("side_data_type", "")) for item in raw_side_data)
        dovi_profile = next((_integer(item.get("dv_profile")) for item in raw_side_data
                             if "dovi" in str(item.get("side_data_type", "")).lower()
                             or "dolby vision" in str(item.get("side_data_type", "")).lower()), None)
        streams.append(Stream(
            index=int(raw.get("index", len(streams))),
            codec_type=str(raw.get("codec_type", "unknown")),
            codec_name=str(raw.get("codec_name", "unknown")).lower(),
            codec_long_name=str(raw.get("codec_long_name", "")),
            width=int(raw.get("width", 0) or 0),
            height=int(raw.get("height", 0) or 0),
            bit_rate=_integer(raw.get("bit_rate")),
            channels=int(raw.get("channels", 0) or 0),
            channel_layout=str(raw.get("channel_layout", "")),
            profile=str(raw.get("profile", "")),
            pixel_format=str(raw.get("pix_fmt", "")),
            color_primaries=str(raw.get("color_primaries", "")),
            color_space=str(raw.get("color_space", "")),
            color_transfer=str(raw.get("color_transfer", "")),
            side_data=side_data,
            dolby_vision_profile=dovi_profile,
            is_default=bool(raw.get("disposition", {}).get("default", 0)),
            language=str(raw.get("tags", {}).get("language", "")).strip().lower(),
        ))
    fmt = data.get("format", {})
    return MediaInfo(path=path, duration=_number(fmt.get("duration")),
                     bit_rate=_integer(fmt.get("bit_rate")),
                     size=int(fmt.get("size", path.stat().st_size) or 0),
                     streams=tuple(streams), format_name=str(fmt.get("format_name", "")))
