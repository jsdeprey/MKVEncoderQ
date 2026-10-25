from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    input_directory: str = "input"
    output_directory: str = ""
    temp_directory: str = "temp"
    backup_originals: bool = True
    backup_suffix: str = ".bak"
    preserve_metadata: bool = False
    video_bit_depth: int = 10
    quality_rf_by_resolution: dict[str, float] = field(default_factory=lambda: {
        "2160": 22.5, "1080": 22.5, "720": 23.0, "480": 23.0,
    })
    encoder_preset: str = "fast"
    crop_mode: str = "auto"
    denoise_mode: str = "off"
    hdr_denoise_mode: str = "off"
    always_reencode: bool = False
    minimum_projected_savings_percent: float = 10.0
    sample_count: int = 3
    sample_duration_seconds: float = 10.0
    reanalysis_mode: str = "progressive"
    progressive_sample_max: int = 6
    audio_fallback_codec: str = "aac"
    audio_passthrough_codecs: tuple[str, ...] = (
        "aac", "ac3", "eac3", "truehd", "dts", "dtshd",
    )
    audio_stream_mode: str = "first_english"
    audio_bitrate_per_channel: int = 64_000
    minimum_audio_bitrate: int = 128_000
    duration_tolerance_seconds: float = 2.0
    encoding_backend: str = "handbrake"
    handbrake_cli: str = "HandBrakeCLI"
    ffprobe: str = "ffprobe"
    watch_input_directory: bool = True
    select_new_files_for_encoding: bool = True
    inspect_new_files: bool = True
    clean_new_encodes: bool = True
    mark_filenames_with_spaces: bool = True
    mark_filenames_missing_year_or_episode: bool = True
    file_ready_seconds: float = 10.0
    rescan_interval_seconds: float = 5.0
    theme: str = "dark"


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    return application_directory() / "config.json"


def create_default_config(path: Path) -> None:
    default = Config()
    values = {name: getattr(default, name) for name in Config.__dataclass_fields__}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    for setting in (default.input_directory, default.temp_directory):
        folder = Path(setting)
        if not folder.is_absolute():
            folder = path.parent / folder
        folder.mkdir(parents=True, exist_ok=True)


def save_config(config: Config, path: Path | None = None) -> None:
    """Validate and atomically save a configuration."""
    if path is None:
        path = default_config_path()
    _validate_config(config)
    values = {name: getattr(config, name) for name in Config.__dataclass_fields__}
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError(f"Cannot save configuration {path}: {exc}") from exc


def _validate_config(config: Config) -> None:
    if not config.input_directory.strip() or not config.temp_directory.strip():
        raise ValueError("input_directory and temp_directory cannot be blank")
    if not config.backup_suffix:
        raise ValueError("backup_suffix cannot be blank")
    if config.video_bit_depth not in {8, 10, 12}:
        raise ValueError("video_bit_depth must be 8, 10, or 12")
    if config.encoding_backend != "handbrake":
        raise ValueError("encoding_backend must be handbrake")
    if any(not 0 <= value <= 51 for value in config.quality_rf_by_resolution.values()):
        raise ValueError("quality RF values must be between 0 and 51")
    if config.denoise_mode not in {"off", "light", "medium", "strong"}:
        raise ValueError("denoise_mode must be off, light, medium, or strong")
    if config.crop_mode not in {"auto", "conservative", "none"}:
        raise ValueError("crop_mode must be auto, conservative, or none")
    if config.hdr_denoise_mode not in {"off", "light", "medium", "strong"}:
        raise ValueError("hdr_denoise_mode must be off, light, medium, or strong")
    if config.audio_stream_mode not in {"all", "first", "default", "first_english"}:
        raise ValueError("audio_stream_mode must be all, first, default, or first_english")
    supported_passthrough = {
        "aac", "ac3", "eac3", "truehd", "dts", "dtshd", "mp2", "mp3",
        "opus", "vorbis", "flac", "alac",
    }
    unknown_passthrough = sorted(set(config.audio_passthrough_codecs)
                                 - supported_passthrough)
    if unknown_passthrough:
        raise ValueError("unsupported audio passthrough codec(s): "
                         + ", ".join(unknown_passthrough))
    if config.audio_fallback_codec != "aac":
        raise ValueError("audio_fallback_codec must currently be aac")
    if config.audio_bitrate_per_channel <= 0 or config.minimum_audio_bitrate <= 0:
        raise ValueError("audio bitrates must be greater than zero")
    if config.sample_count < 1:
        raise ValueError("sample_count must be at least 1")
    if config.sample_duration_seconds <= 0:
        raise ValueError("sample_duration_seconds must be greater than 0")
    if config.reanalysis_mode not in {"progressive", "new_set"}:
        raise ValueError("reanalysis_mode must be progressive or new_set")
    if config.progressive_sample_max < config.sample_count:
        raise ValueError("progressive_sample_max must be at least sample_count")
    if config.file_ready_seconds < 0:
        raise ValueError("file_ready_seconds must be zero or greater")
    if config.rescan_interval_seconds <= 0:
        raise ValueError("rescan_interval_seconds must be greater than 0")
    if config.duration_tolerance_seconds < 0:
        raise ValueError("duration_tolerance_seconds must be zero or greater")
    if not 0 <= config.minimum_projected_savings_percent <= 100:
        raise ValueError("minimum_projected_savings_percent must be between 0 and 100")
    if config.theme not in {"light", "dark"}:
        raise ValueError("theme must be light or dark")


def load_config(path: Path | None) -> Config:
    if path is None:
        path = default_config_path()
    if not path.exists():
        create_default_config(path)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read configuration {path}: {exc}") from exc
    # Accept and migrate settings written by older versions.
    obsolete_settings = ("backup_directory_name", "quality_crf",
                         "hdr_bitrate_targets", "hdr_maxrate_multiplier",
                         "hdr_buffer_multiplier", "h264_bitrate_thresholds",
                         "video_encoder", "ffmpeg", "dovi_tool")
    needs_migration = any(name in values for name in obsolete_settings)
    for obsolete in obsolete_settings:
        values.pop(obsolete, None)
    known = Config.__dataclass_fields__
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ValueError(f"Unknown configuration setting(s): {', '.join(unknown)}")
    needs_migration = needs_migration or set(values) != set(known)
    config = Config(**values)
    _validate_config(config)
    if needs_migration:
        migrated = {name: getattr(config, name) for name in known}
        try:
            path.write_text(json.dumps(migrated, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Cannot update configuration {path}: {exc}") from exc
    return config
