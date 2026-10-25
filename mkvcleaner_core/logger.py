from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import MediaFile


class RunLogger:
    def __init__(self, directory: Path, json_logging: bool = True) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.text_path = self.directory / f"mkvclean_{stamp}.log"
        self.json_path = self.directory / f"mkvclean_{stamp}.json"
        self.json_logging = json_logging
        self.records: list[dict] = []

    def add(self, media: MediaFile) -> None:
        record = {
            "file": str(media.path),
            "status": media.status,
            "reasons": list(media.clean_reasons),
            "seconds": round(media.processing_time, 3),
            "original_size": media.original_size,
            "cleaned_size": media.cleaned_size,
            "bytes_saved": media.bytes_saved,
            "backup": str(media.backup_path) if media.backup_path else None,
            "verified": media.verified,
            "error": media.error or None,
            "preflight_changes": list(media.preflight_changes),
        }
        self.records.append(record)

        with self.text_path.open("a", encoding="utf-8") as handle:
            reason_text = ", ".join(record["reasons"]) if record["reasons"] else "-"
            error_text = record["error"] or "-"
            change_text = (", ".join(record["preflight_changes"])
                           if record["preflight_changes"] else "-")
            handle.write(
                f'{record["status"]:8} | {record["file"]} | '
                f'reasons={reason_text} | preflight={change_text} | '
                f'error={error_text}\n'
            )

    def finalize(self) -> None:
        if self.json_logging:
            self.json_path.write_text(
                json.dumps(self.records, indent=2),
                encoding="utf-8",
            )
