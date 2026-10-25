from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from .config import Config
from .benefit import analyze_benefit
from .models import Decision, ScanResult
from .probe import probe

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts",
                    ".mts", ".wmv", ".mpg", ".mpeg", ".vob", ".webm"}


def discover(root: Path, backup_name: str, excluded_root: Path | None = None) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            if excluded_root is not None and path.is_relative_to(excluded_root):
                continue
            try:
                relative_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if backup_name not in relative_parts and ".video-optimizer-" not in path.name:
                yield path


def discover_inventory(
        root: Path, backup_name: str, excluded_root: Path | None = None,
        progress: Callable[[str, int, int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        metadata: Callable[[Path, int, int], None] | None = None,
        folder_delay_seconds: float = 0.0) -> Iterable[Path]:
    """Efficiently inventory a tree while reporting folder/file progress.

    The base directory is enumerated once.  ``progress`` receives the phase
    (``base`` or ``recursive``), folders visited, filesystem files seen, and
    top-level folder count.  Directory links are not followed.
    """
    root = root.resolve()
    try:
        base_entries = list(os.scandir(root))
    except OSError:
        raise
    top_level_folders = sum(
        1 for entry in base_entries if entry.is_dir(follow_symlinks=False))
    if progress:
        progress("base", 1, 0, top_level_folders)
    stack: list[tuple[Path, list[os.DirEntry[str]] | None]] = [(root, base_entries)]
    folders_visited = 0
    files_seen = 0
    while stack:
        if cancelled and cancelled():
            return
        folder, supplied_entries = stack.pop()
        folders_visited += 1
        try:
            entries = supplied_entries if supplied_entries is not None else list(os.scandir(folder))
        except OSError:
            if progress:
                progress("recursive", folders_visited, files_seen, top_level_folders)
            continue
        child_folders: list[Path] = []
        for entry in entries:
            if cancelled and cancelled():
                return
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    child_folders.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            files_seen += 1
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if excluded_root is not None and path.is_relative_to(excluded_root):
                continue
            try:
                relative_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if backup_name not in relative_parts and ".video-optimizer-" not in path.name:
                if metadata:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        metadata(path, stat.st_size, stat.st_mtime_ns)
                    except OSError:
                        pass
                yield path
        stack.extend((path, None) for path in reversed(child_folders))
        if progress:
            progress("recursive", folders_visited, files_seen, top_level_folders)
        if folder_delay_seconds > 0:
            # Deliberately yield between directories on large background NAS
            # scans.  Keep cancellation responsive rather than sleeping for a
            # long uninterrupted interval.
            remaining = folder_delay_seconds
            while remaining > 0:
                if cancelled and cancelled():
                    return
                pause = min(remaining, 0.05)
                time.sleep(pause)
                remaining -= pause


def scan_file(path: Path, config: Config, ffprobe: str = "ffprobe",
              encoder: str = "HandBrakeCLI",
              sample_variant: int = 0, prior_samples=(),
              progressive_target: int | None = None) -> ScanResult:
    try:
        return analyze_benefit(
            probe(path, ffprobe), config, encoder, sample_variant,
            tuple(prior_samples), progressive_target)
    except Exception as exc:  # keep a damaged file from aborting a large run
        return ScanResult(path, Decision.ERROR, str(exc))


def inspect_for_forced_encode(path: Path, config: Config,
                              ffprobe: str = "ffprobe") -> ScanResult:
    """Probe a forced file without running sample encodes."""
    try:
        media = probe(path, ffprobe)
        if not media.videos:
            return ScanResult(path, Decision.SKIP, "No video stream", media)
        if media.is_dolby_vision and media.videos[0].dolby_vision_profile == 5:
            return ScanResult(
                path, Decision.SKIP,
                "Dolby Vision profile 5 cannot be safely re-encoded", media)
        warnings = ((f"Omitting {len(media.data_streams)} unsupported data stream(s) "
                     "from MKV output",) if media.data_streams else ())
        if media.attachments:
            warnings += (f"Omitting {len(media.attachments)} Matroska attachment(s) "
                         "from output",)
        if (config.audio_stream_mode == "default" and media.audios
                and not any(audio.is_default for audio in media.audios)):
            warnings += ("No default audio stream is marked; keeping the first audio stream",)
        if (config.audio_stream_mode == "first_english" and media.audios
                and not any(audio.language.lower() in {"eng", "en", "english"}
                            for audio in media.audios)):
            warnings += ("No English audio stream is tagged; keeping the first audio stream",)
        return ScanResult(path, Decision.RECOMMEND,
                          "Forced encode; sampling skipped", media,
                          warnings=warnings)
    except Exception as exc:
        return ScanResult(path, Decision.ERROR, str(exc))


def scan(root: Path, config: Config, ffprobe: str = "ffprobe",
         encoder: str = "HandBrakeCLI",
         progress: Callable[[Path], None] | None = None,
         excluded_root: Path | None = None) -> list[ScanResult]:
    results = []
    for path in discover(root, "_video_optimizer_backups", excluded_root):
        if progress:
            progress(path)
        results.append(scan_file(path, config, ffprobe, encoder))
    return results
