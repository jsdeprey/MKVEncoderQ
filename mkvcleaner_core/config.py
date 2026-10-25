from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CleanerConfig:
    default_input_directory: str = ""
    inspection_mode: str = "stream"
    recursive: bool = True
    keep_backup: bool = False
    verify_output: bool = True
    preserve_timestamps: bool = True
    dry_run: bool = False
    threads: int = 4

    remove_title: bool = True
    remove_video_track_names: bool = False
    remove_audio_track_names: bool = False
    remove_subtitle_track_names: bool = False
    disable_default_subtitles_for_english_audio: bool = True
    clear_all_subtitle_default_flags: bool = False
    clear_all_subtitle_forced_flags: bool = False
    tag_cleanup: str = "all"
    remove_chapters: bool = False
    remove_attachments: bool = True

    log_directory: str = "logs"
    json_logging: bool = True
    show_skipped: bool = False
    mkvmerge_path: str | None = None
    theme: str = "dark"

    def validate(self) -> None:
        self.inspection_mode = str(self.inspection_mode).strip().lower()
        if self.inspection_mode not in {"stream", "full"}:
            raise ValueError('inspection_mode must be either "stream" or "full"')
        if not (1 <= self.threads <= 8):
            raise ValueError("'threads' must be between 1 and 8.")
        if self.default_input_directory and not self.default_input_directory.strip():
            raise ValueError("'default_input_directory' must be empty or a valid path string.")
        if not self.log_directory.strip():
            raise ValueError("'log_directory' cannot be empty.")
        self.tag_cleanup = str(self.tag_cleanup).strip().lower()
        if self.tag_cleanup not in {"all", "global_only", "track_only", "none"}:
            raise ValueError(
                "'tag_cleanup' must be all, global_only, track_only, or none."
            )
        self.theme = str(self.theme).strip().lower()
        if self.theme not in {"light", "dark"}:
            raise ValueError("'theme' must be either \"light\" or \"dark\".")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def remove_global_tags(self) -> bool:
        return self.tag_cleanup in {"all", "global_only"}

    @property
    def remove_track_tags(self) -> bool:
        return self.tag_cleanup in {"all", "track_only"}

    def remove_name_for_track(self, track_type: str) -> bool:
        normalized = str(track_type).strip().lower()
        return {
            "video": self.remove_video_track_names,
            "audio": self.remove_audio_track_names,
            "subtitle": self.remove_subtitle_track_names,
            "subtitles": self.remove_subtitle_track_names,
        }.get(normalized, False)


def save_config(config: CleanerConfig, path: Path) -> None:
    config.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.to_dict(), indent=4) + "\n",
        encoding="utf-8",
    )


def create_default_config(path: Path) -> CleanerConfig:
    config = CleanerConfig()
    save_config(config, path)
    return config


def load_config(path: Path | None = None) -> CleanerConfig:
    config = CleanerConfig()

    if path is None:
        config.validate()
        return config

    if not path.exists():
        return create_default_config(path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a JSON object: {path}")

    valid_names = {item.name for item in fields(CleanerConfig)}
    # Human-readable note keys in distributed config files begin with an
    # underscore and are intentionally ignored.
    data = {key: value for key, value in data.items() if not key.startswith("_")}

    # Migrate MKVCleaner 3.0 and earlier configurations automatically.
    old_global = data.pop("remove_global_tags", None)
    old_track = data.pop("remove_track_tags", None)
    old_track_names = data.pop("remove_track_names", None)
    if old_track_names:
        data.setdefault("remove_video_track_names", True)
        data.setdefault("remove_audio_track_names", True)
        data.setdefault("remove_subtitle_track_names", True)
    if "tag_cleanup" not in data and (old_global is not None or old_track is not None):
        remove_global = True if old_global is None else bool(old_global)
        remove_track = True if old_track is None else bool(old_track)
        data["tag_cleanup"] = {
            (True, True): "all",
            (True, False): "global_only",
            (False, True): "track_only",
            (False, False): "none",
        }[(remove_global, remove_track)]
    unknown = sorted(set(data) - valid_names)
    if unknown:
        raise ValueError(
            "Unknown configuration option(s): " + ", ".join(unknown)
        )

    config = CleanerConfig(**data)
    config.validate()
    return config
