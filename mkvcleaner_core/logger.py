from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import MediaFile
from video_optimizer.tab_logging import get_logger, read_settings


class RunLogger:
    def __init__(self, directory: Path, json_logging: bool = True) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.logger = get_logger("cleaner")
        self.text_path = Path(self.logger.handlers[0].baseFilename)
        self.json_path = self.text_path.parent / "cleaner-latest.json"
        # Bound historical per-run reports from earlier releases as well.
        keep = read_settings("cleaner")["backups"]
        for pattern in ("mkvclean_*.log", "mkvclean_*.json"):
            old = sorted(self.directory.glob(pattern), key=lambda p:p.stat().st_mtime, reverse=True)
            for path in old[keep:]: path.unlink(missing_ok=True)
        self.json_logging = json_logging
        self.records: list[dict] = []
        self.record_bytes = 2

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
        self.record_bytes += len(json.dumps(record, separators=(",", ":")).encode("utf-8")) + 1

        self.logger.info("%s | %s | reasons=%s | preflight=%s | error=%s",
                         record['status'], record['file'], record['reasons'],
                         record['preflight_changes'], record['error'])
        # Keep the latest optional JSON snapshot within the same configured size limit.
        limit = read_settings("cleaner")["max_mb"] * 1024 * 1024
        while self.records and self.record_bytes > limit:
            removed = self.records.pop(0)
            self.record_bytes -= len(json.dumps(removed, separators=(",", ":")).encode('utf-8')) + 1

    def finalize(self) -> None:
        if self.json_logging:
            self.json_path.write_text(
                json.dumps(self.records, separators=(",", ":")),
                encoding="utf-8",
            )
