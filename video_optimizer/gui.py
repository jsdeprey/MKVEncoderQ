from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
import tkinter as tk
import uuid
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .config import Config, application_directory, default_config_path, load_config, save_config
from .encoder import (BackupAlreadyExists, EncodeCancelled,
                      EncodeVerificationFailed, encode)
from .drop import expand_dropped_paths
from .inspection import (InspectionCache, MediaInspection, inspect_media,
                         inspection_from_media, inspection_matches,
                         inspection_sort_key)
from .models import Decision
from .queueing import LiveQueue
from .scanner import VIDEO_EXTENSIONS, inspect_for_forced_encode, scan_file
from .tools import resolve_program


# Treeview values retain their stable logical field order.  Only this tuple
# controls the left-to-right presentation, so future column moves do not
# require rewriting row data or click handlers.
ENCODER_DISPLAY_COLUMNS = (
    "handle", "file", "path", "media", "rating", "status", "savings",
    "current_size", "projected", "decision", "action", "backup",
    "selected", "force",
)


def _display_column_number(name: str) -> str:
    return f"#{ENCODER_DISPLAY_COLUMNS.index(name) + 1}"


HANDBRAKE_CLI_DOWNLOAD_URL = "https://handbrake.fr/downloads2.php"
FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"
FAILED_DETAILS_TEXT = "Failed — see File Details"


def _filename_text(path: Path, mark_spaces: bool = True,
                   mark_missing_year_or_episode: bool = True) -> str:
    """Prefix filenames needing attention without changing the real name."""
    name = Path(path).name
    stem = Path(path).stem
    has_year = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", stem)
    has_episode = re.search(r"(?i)(?<![a-z0-9])s\d{1,2}e\d{1,3}(?!\d)", stem)
    return (f"🔸 {name}"
            if ((mark_spaces and " " in name)
                or (mark_missing_year_or_episode
                    and has_year is None and has_episode is None))
            else name)


def _encoder_play_path(
        source: Path, status: str,
        completed_outputs: dict[Path, Path]) -> Path | None:
    """Return the safe media path for an Encoder row, if one is playable."""
    source = source.resolve()
    if status in {"Sampling", "Encoding", "Paused", "Missing"}:
        return None
    if status == "Completed":
        output = completed_outputs.get(source)
        if output is not None and output.is_file():
            return output
    return source if source.is_file() else None


def _priority_position(rows: list[tuple[str, str]], target: str) -> int:
    """Insert after active/priority work, preserving finished history above."""
    statuses = [status for iid, status in rows if iid != target]
    active = [i for i, status in enumerate(statuses)
              if status in {"Encoding", "Paused", "Sampling"}]
    position = (active[-1] + 1 if active else next(
        (i for i, status in enumerate(statuses) if status in {
            "Queued", "Waiting", "Checking file...", "Waiting to scan"}),
        len(statuses)))
    while position < len(statuses) and statuses[position] == "Queued":
        position += 1
    return position


def _moved_row_order(rows: list[str], selected: list[str], destination: str) -> list[str]:
    """Move selected rows as one stable block or one step without scrambling them."""
    selected_set = set(selected)
    chosen = [row for row in rows if row in selected_set]
    if not chosen:
        return rows
    if destination == "top":
        return chosen + [row for row in rows if row not in selected_set]
    if destination == "bottom":
        return [row for row in rows if row not in selected_set] + chosen
    result = list(rows)
    if destination == "up":
        for index in range(1, len(result)):
            if result[index] in selected_set and result[index - 1] not in selected_set:
                result[index - 1], result[index] = result[index], result[index - 1]
    elif destination == "down":
        for index in range(len(result) - 2, -1, -1):
            if result[index] in selected_set and result[index + 1] not in selected_set:
                result[index], result[index + 1] = result[index + 1], result[index]
    return result


def _size_text(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "—"


def _duration_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _inspection_summary(discovered: int, done: int, failed: int,
                        state: str, current: str = "", eta: float | None = None) -> str:
    """Build a persistent inspection status that never hides the totals."""
    labels = {
        "running": "Inspecting",
        "waiting": "Inspection caught up; discovery still running",
        "complete": "Inspection complete",
        "cancelled": "Inspection cancelled",
        "off": "Inspection off",
    }
    text = (f"{labels[state]} — {done:,} of {discovered:,} inspected • "
            f"{max(0, done - failed):,} succeeded • {failed:,} failed")
    if current:
        text += f" • {current}"
    if eta is not None:
        text += f" • ETA {_duration_text(eta)}"
    return text


def _clock_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _percent_text(value: float) -> str:
    return f"{max(0.0, min(100.0, value)):.1f}%"


def _completed_progress(detail: str) -> tuple[float, str]:
    """Return the final progress update for a successfully encoded file."""
    return 100.0, detail


def _completed_result(bytes_before: int, bytes_after: int,
                      elapsed: str, tolerance: int = 1024 * 1024) -> tuple[str, bool]:
    """Put final size first and flag a materially larger successful output."""
    difference = bytes_after - bytes_before
    if difference > tolerance:
        percent = difference / bytes_before * 100 if bytes_before else 0.0
        return (f"{_size_text(bytes_after)} output • "
                f"{_size_text(difference)} larger than source ({percent:.1f}%) • "
                f"{elapsed}", True)
    saved = ((bytes_before - bytes_after) / bytes_before * 100
             if bytes_before else 0.0)
    return f"{_size_text(bytes_after)} output • {saved:.1f}% saved • {elapsed}", False


def _preserve_completed_refresh(
        previous_values, status: str, progress: str, savings: str,
        current_size: str, projected: str, decision: str,
) -> tuple[str, str, str, str, str]:
    """Keep a directory rescan from erasing a completed row's encode data."""
    if (status == "Completed" and len(previous_values) > 8
            and str(previous_values[3]) == "Completed"):
        return tuple(str(value) for value in previous_values[4:9])
    return progress, savings, current_size, projected, decision


def _should_queue_row_inspection(enabled: bool, missing: bool,
                                 inventory_active: bool) -> bool:
    """Keep displayed inventory rows from starting ffprobe prematurely."""
    return enabled and not missing and not inventory_active


def _inventory_decision(item, inspected: bool, inspect_enabled: bool) -> str:
    """Describe inventory state without erasing a finished processing result."""
    if item.status in {"Skipped", "Failed", "Cancelled", "Reverted"}:
        return item.detail
    if item.status == "Not selected":
        return "Excluded from processing"
    if inspected:
        return "Media inspected — Ready"
    return ("Inventory found — media inspection pending"
            if inspect_enabled else "Awaiting Start")


def _newly_missing_items(previous_statuses: dict[Path, str], items) -> list:
    """Return removals that discovery batches cannot report by definition."""
    return [item for item in items
            if item.status == "Missing"
            and previous_statuses.get(item.path) != "Missing"]


def _backup_path(source: Path, suffix: str) -> Path:
    """Return the exact backup filename used by the encoder."""
    return source.with_name(source.name + suffix)


def _restore_backup_transaction(source: Path, output: Path, backup: Path) -> None:
    """Restore a backup while retaining the encoded output until commit."""
    source, output, backup = map(Path, (source, output, backup))
    if not backup.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup}")
    if source != output and source.exists():
        raise FileExistsError(f"Original filename already exists: {source}")
    temporary: Path | None = None
    restored = False
    try:
        if output.is_file():
            temporary = output.with_name(
                f"mkvencoderq-revert-{uuid.uuid4().hex}.tmp")
            output.rename(temporary)
        backup.rename(source)
        restored = True
        if temporary is not None:
            temporary.unlink()
    except OSError:
        if restored and source.exists() and not backup.exists():
            source.rename(backup)
        if temporary is not None and temporary.exists() and not output.exists():
            temporary.rename(output)
        raise


def _scan_status_text(enabled: bool, scanning: bool = False,
                      last_scan: str | None = None,
                      session_running: bool = True,
                      item_count: int | None = None,
                      next_scan_seconds: float | None = None,
                      encoding: bool = False) -> str:
    if not enabled:
        return ""
    if item_count is None:
        if not session_running:
            return (f"● Watching input folder • Last scan {last_scan} • press Start to encode"
                    if last_scan else "● Watching input folder; press Start to encode")
        if scanning:
            return "● Scanning selected folder now..."
        if last_scan:
            return f"● Last scan {last_scan}"
        return "● Waiting for next folder scan"
    mode = "Large-library mode" if item_count >= 250 else "Normal mode"
    parts = [f"● {mode}"]
    if scanning:
        parts.append("Background scan in progress")
    elif next_scan_seconds is not None:
        remaining = max(0.0, next_scan_seconds)
        if remaining < 90:
            parts.append(f"Next scan in ~{max(1, int(round(remaining)))} sec")
        else:
            parts.append(f"Next scan in ~{max(1, int((remaining + 59) // 60))} min")
    if encoding and item_count >= 250:
        parts.append("Background scanning throttled during encoding")
    if last_scan:
        parts.append(f"Last scan {last_scan}")
    if not session_running:
        parts.append("press Start to encode")
    return " • ".join(parts)


def _adaptive_rescan_interval(configured: float, video_count: int,
                              previous_duration: float = 0.0) -> float:
    """Keep large-library polling useful without repeatedly loading a NAS."""
    if video_count >= 3000:
        size_interval = 900.0
    elif video_count >= 1000:
        size_interval = 300.0
    elif video_count >= 250:
        size_interval = 60.0
    else:
        size_interval = configured
    return max(configured, size_interval, previous_duration * 4.0)


def _background_folder_delay(video_count: int, encoding: bool) -> float:
    """Yield between folders during large repeat scans, especially encoding."""
    if video_count >= 3000:
        return 0.03 if encoding else 0.01
    if video_count >= 1000:
        return 0.015 if encoding else 0.005
    if video_count >= 250 and encoding:
        return 0.005
    return 0.0


def _can_reanalyze(status: str, running: bool) -> bool:
    """Return whether a file row may start a fresh sampling pass."""
    return status in {"Skipped", "Failed", "Cancelled", "Reverted", "Queued"}


def _show_reanalyze(status: str) -> bool:
    """Return whether a finished row should display the reanalysis action."""
    return status not in {
        "Completed", "Encoding", "Sampling", "Paused", "Waiting",
        "Waiting to scan", "Checking file...", "Not selected", "Missing",
    }


def _reanalyze_action_text(active: bool = False, queued: bool = False) -> str:
    """Return a compact button-like label for a Treeview action cell."""
    if queued:
        return "[ Queued ]"
    return "[ Reanalyzing... ]" if active else "[ Reanalyze ]"


def _progressive_action_text(count: int, maximum: int, *, active: bool = False,
                             queued: bool = False) -> str:
    """Keep progressive reanalysis actions direct and easy to recognize."""
    if active:
        return _reanalyze_action_text(active=True)
    if queued:
        return _reanalyze_action_text(queued=True)
    if count >= maximum:
        return f"[ Maximum {maximum}/{maximum} ]"
    return _reanalyze_action_text()


def _sampling_signature(config: Config) -> tuple:
    """Settings whose changes make retained sample outputs incomparable."""
    return (
        config.sample_count, config.sample_duration_seconds,
        config.progressive_sample_max, config.video_bit_depth,
        tuple(sorted(config.quality_rf_by_resolution.items())),
        config.encoder_preset, config.crop_mode, config.denoise_mode,
        config.hdr_denoise_mode, config.audio_stream_mode,
        tuple(config.audio_passthrough_codecs), config.audio_fallback_codec,
        config.audio_bitrate_per_channel, config.minimum_audio_bitrate,
    )


def _row_action_text(status: str, *, reanalysis_active: bool = False,
                     reanalysis_queued: bool = False) -> str:
    """Return the useful quick-action or active-state label for a row."""
    if reanalysis_queued:
        return _reanalyze_action_text(queued=True)
    if reanalysis_active:
        return _reanalyze_action_text(active=True)
    if status == "Sampling":
        return "[ Sampling... ]"
    if status == "Encoding":
        return "[ Encoding... ]"
    if status == "Paused":
        return "[ Paused ]"
    if status == "Completed":
        return "[ Play ]"
    if status == "Queued":
        return "—"
    return _reanalyze_action_text() if _show_reanalyze(status) else "—"


def _force_text(enabled: bool) -> str:
    return "☑ Force" if enabled else "☐ Force"


def _force_all_text(states: list[bool]) -> str:
    if states and all(states):
        mark = "☑"
    elif states and any(states):
        mark = "◩"
    else:
        mark = "☐"
    return f"{mark} Force All"


def _encode_text(enabled: bool) -> str:
    return "☑ Encode" if enabled else "☐ Encode"


def _clean_text(enabled: bool, available: bool = True) -> str:
    if not available:
        return "— Unavailable"
    return "☑ Clean" if enabled else "☐ Clean"


def _idle_row_needs_update(values, item) -> bool:
    """Return whether idle discovery changed anything displayed in a row."""
    if not values:
        return True
    expected_detail = item.detail if item.status == "Missing" else "Awaiting Start"
    return (str(values[3]) != item.status
            or str(values[6]) != _size_text(item.size)
            or str(values[8]) != expected_detail)


def _clean_all_text(states: list[bool], available: bool = True) -> str:
    if not available:
        return "— Clean All"
    if states and all(states):
        mark = "☑"
    elif states and any(states):
        mark = "◩"
    else:
        mark = "☐"
    return f"{mark} Clean All"


def _encode_all_text(states: list[bool]) -> str:
    if states and all(states):
        mark = "☑"
    elif states and any(states):
        mark = "◩"
    else:
        mark = "☐"
    return f"{mark} Encode All"


def _file_count_text(total: int, filtered: int, selected: int) -> str:
    return (f"Files: {total:,} total / {filtered:,} filtered / "
            f"{selected:,} selected")


def _can_select(status: str) -> bool:
    return status in {
        "Checking file...", "Waiting to scan", "Waiting", "Not selected", "Reverted"}


def _can_reorder(status: str) -> bool:
    return status in {"Checking file...", "Waiting to scan", "Waiting", "Queued",
                      "Not selected"}


def _can_force(status: str) -> bool:
    return status in {
        "Checking file...", "Waiting to scan", "Waiting",
        "Skipped", "Failed", "Cancelled", "Reverted",
    }


def _can_clean(status: str) -> bool:
    return status != "Missing"


def _clean_after_encode_requested(default_enabled: bool, path: Path,
                                  selected_paths: set[Path]) -> bool:
    """Return whether a successful output should be handed to Cleaner."""
    return default_enabled or path.resolve() in selected_paths


def _logical_rename_mapping(
        renamed_files: dict[Path, Path], completed_outputs: dict[Path, Path],
        displayed_paths: set[Path]) -> dict[Path, Path]:
    """Map renamed completed outputs back to their logical Encoder rows."""
    logical = {
        old: new for old, new in renamed_files.items()
        if old in displayed_paths
    }
    for source, output in completed_outputs.items():
        if source in displayed_paths and output in renamed_files:
            logical[source] = renamed_files[output]
    return logical


def _sampling_filename_text(path: Path) -> str:
    """Return the heading shown before a file's individual sample results."""
    return f"File: {path.name}"


def _row_tags(status: str, larger_output: bool = False) -> tuple[str, ...]:
    """Return the visual emphasis tag for noteworthy row states."""
    if status == "Completed" and larger_output:
        return ("larger_output",)
    return {
        "Missing": ("missing",),
        "Sampling": ("sampling",),
        "Encoding": ("encoding",),
        "Paused": ("paused",),
    }.get(status, ())


class DirectoryToggle(tk.Canvas):
    """Keyboard-accessible switch with explicit ON/OFF text."""
    def __init__(self, parent, enabled: bool, command) -> None:
        super().__init__(parent, width=76, height=26, highlightthickness=0,
                         borderwidth=0, relief="flat", takefocus=True,
                         cursor="hand2")
        self.enabled = enabled
        self.command = command
        self._focused = False
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", lambda _event: self._set_focus(True))
        self.bind("<FocusOut>", lambda _event: self._set_focus(False))
        self.set(enabled)

    def _set_focus(self, focused: bool) -> None:
        self._focused = focused
        self.set(self.enabled)

    def _toggle(self, event=None):
        self.focus_set()
        self.command(not self.enabled)
        return "break"

    def set(self, enabled: bool) -> None:
        self.enabled = enabled
        self.delete("all")
        color = "#176b3a" if enabled else "#62666d"
        outline = "#4f9cf9" if self._focused else color
        width = 2 if self._focused else 1
        self.create_rectangle(13, 2, 63, 24, fill=color, outline=outline,
                              width=width)
        self.create_oval(2, 2, 24, 24, fill=color, outline=outline, width=width)
        self.create_oval(52, 2, 74, 24, fill=color, outline=outline, width=width)
        x = 63 if enabled else 13
        self.create_oval(x-9, 4, x+9, 22, fill="white", outline="white")
        self.create_text(33 if enabled else 44, 13,
                         text="ON" if enabled else "OFF", fill="white",
                         font=("Segoe UI", 9, "bold"))


class LabeledProgress(ttk.Frame):
    """A normal themed progress bar with a readable percentage overlay."""

    def __init__(self, parent) -> None:
        super().__init__(parent, style="ProgressContainer.TFrame")
        self.bar = ttk.Progressbar(self, maximum=100, style="Main.Horizontal.TProgressbar")
        self.bar.pack(fill="x")
        self.text = ttk.Label(self, text="0.0%", style="ProgressText.TLabel",
                              font=("Segoe UI", 10, "bold"))
        self.text.place(relx=.5, rely=.5, anchor="center")

    def set(self, value: float) -> None:
        self.bar["value"] = value
        self.text.configure(text=_percent_text(value))

    def reset(self) -> None:
        self.set(0.0)

    def set_idle(self) -> None:
        """Clear stale file progress while directory watching stays active."""
        self.bar["value"] = 0
        self.text.configure(text="Idle")


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: "OptimizerGUI", config: Config) -> None:
        super().__init__(parent)
        self.parent = parent
        self.config = config
        self.title("MKV Encoder Q Settings")
        self.geometry("720x620")
        self.minsize(650, 540)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.vars: dict[str, tk.Variable] = {}
        self.tool_status = tk.StringVar(value="Select Check Tools to verify required programs.")
        self._build()
        self._test_encoder_tools()

    def _text(self, name: str, value: object) -> tk.StringVar:
        var = tk.StringVar(value=str(value))
        self.vars[name] = var
        return var

    def _bool(self, name: str, value: bool) -> tk.BooleanVar:
        var = tk.BooleanVar(value=value)
        self.vars[name] = var
        return var

    def _row(self, frame, row: int, label: str, name: str, value: object,
             choices: tuple[str, ...] | None = None, browse: bool = False) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        var = self._text(name, value)
        if choices:
            widget = ttk.Combobox(frame, textvariable=var, values=choices, state="readonly")
        else:
            widget = ttk.Entry(frame, textvariable=var)
        widget.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        if browse:
            ttk.Button(frame, text="Browse...", command=lambda: self._choose_folder(var)).grid(
                row=row, column=2, padx=(0, 8), pady=6)

    def _check(self, frame, row: int, label: str, name: str, value: bool) -> None:
        ttk.Checkbutton(frame, text=label, variable=self._bool(name, value)).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=8, pady=6)

    def _tab(self, notebook: ttk.Notebook, title: str) -> ttk.Frame:
        frame = ttk.Frame(notebook, padding=10)
        frame.columnconfigure(1, weight=1)
        notebook.add(frame, text=title)
        return frame

    def _build(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        c = self.config

        general = self._tab(notebook, "Folders & backups")
        self._row(general, 0, "Default input folder", "input_directory", c.input_directory, browse=True)
        self._row(general, 1, "Output folder (blank = input)", "output_directory", c.output_directory, browse=True)
        self._row(general, 2, "Temporary folder", "temp_directory", c.temp_directory, browse=True)
        self._check(general, 3, "Keep a backup of the original file", "backup_originals", c.backup_originals)
        self._row(general, 4, "Backup filename suffix", "backup_suffix", c.backup_suffix)
        self._check(general, 5, "Preserve source metadata", "preserve_metadata", c.preserve_metadata)

        video = self._tab(notebook, "Video & sampling")
        self._row(video, 0, "Encoder preset", "encoder_preset", c.encoder_preset,
                  ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"))
        self._row(video, 1, "Video bit depth", "video_bit_depth", c.video_bit_depth, ("8", "10", "12"))
        self._row(video, 2, "Crop mode", "crop_mode", c.crop_mode, ("auto", "conservative", "none"))
        self._row(video, 3, "Normal-video denoise", "denoise_mode", c.denoise_mode,
                  ("off", "light", "medium", "strong"))
        self._row(video, 4, "HDR-video denoise", "hdr_denoise_mode", c.hdr_denoise_mode,
                  ("off", "light", "medium", "strong"))
        for index, height in enumerate(("2160", "1080", "720", "480"), start=5):
            self._row(video, index, f"{height}p quality RF", f"rf_{height}", c.quality_rf_by_resolution[height])

        sampling = self._tab(notebook, "Decision")
        self._row(sampling, 0, "Minimum projected savings (%)", "minimum_projected_savings_percent", c.minimum_projected_savings_percent)
        self._row(sampling, 1, "Number of samples", "sample_count", c.sample_count)
        self._row(sampling, 2, "Seconds per sample", "sample_duration_seconds", c.sample_duration_seconds)
        reanalysis_labels = {
            "progressive": "Progressively add one sample",
            "new_set": "Use a new sample set",
        }
        self._row(sampling, 3, "Reanalyze behavior", "reanalysis_mode",
                  reanalysis_labels.get(c.reanalysis_mode, c.reanalysis_mode),
                  tuple(reanalysis_labels.values()))
        self._row(sampling, 4, "Maximum progressive samples",
                  "progressive_sample_max", c.progressive_sample_max)
        self._row(sampling, 5, "Duration verification tolerance (seconds)", "duration_tolerance_seconds", c.duration_tolerance_seconds)

        audio = self._tab(notebook, "Audio")
        audio_labels = {
            "all": "All",
            "first": "First",
            "default": "Default",
            "first_english": "First English",
        }
        self._row(audio, 0, "Audio streams to keep", "audio_stream_mode",
                  audio_labels.get(c.audio_stream_mode, c.audio_stream_mode),
                  tuple(audio_labels.values()))
        self._row(audio, 1, "Passthrough codecs (comma separated)", "audio_passthrough_codecs",
                  ", ".join(c.audio_passthrough_codecs))
        self._row(audio, 2, "AAC bitrate per channel (bps)", "audio_bitrate_per_channel", c.audio_bitrate_per_channel)
        self._row(audio, 3, "Minimum AAC bitrate (bps)", "minimum_audio_bitrate", c.minimum_audio_bitrate)

        watching = self._tab(notebook, "Watching & tools")
        self._check(watching, 0, "Keep watching the input folder for new files", "watch_input_directory", c.watch_input_directory)
        self._row(watching, 1, "File stability wait (seconds)", "file_ready_seconds", c.file_ready_seconds)
        self._row(watching, 2, "Folder rescan interval (seconds)", "rescan_interval_seconds", c.rescan_interval_seconds)
        self._row(watching, 3, "HandBrakeCLI program or path", "handbrake_cli", c.handbrake_cli)
        self._row(watching, 4, "ffprobe program or path", "ffprobe", c.ffprobe)
        ttk.Label(
            watching,
            text=(
                "HandBrakeCLI.exe is the separate HandBrake command-line program. "
                "ffprobe.exe is included in Windows FFmpeg build packages. You may "
                "copy both EXE files beside MKVEncoderQ.exe or select their paths above."
            ),
            wraplength=610,
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 2))
        tool_buttons = ttk.Frame(watching)
        tool_buttons.grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=6)
        ttk.Button(tool_buttons, text="Check Tools", command=self._test_encoder_tools).pack(side="left")
        ttk.Button(
            tool_buttons, text="Get HandBrakeCLI…",
            command=lambda: webbrowser.open(HANDBRAKE_CLI_DOWNLOAD_URL),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            tool_buttons, text="Get FFmpeg (includes ffprobe)…",
            command=lambda: webbrowser.open(FFMPEG_DOWNLOAD_URL),
        ).pack(side="left", padx=(8, 0))
        ttk.Label(watching, textvariable=self.tool_status, wraplength=560).grid(
            row=7, column=0, columnspan=3, sticky="w", padx=8, pady=6)

        appearance = self._tab(notebook, "Appearance")
        self._row(appearance, 0, "Color mode", "theme", c.theme, ("light", "dark"))
        self._check(appearance, 1, "Mark filenames containing spaces with an orange diamond",
                    "mark_filenames_with_spaces", c.mark_filenames_with_spaces)
        self._check(appearance, 2, "Mark filenames missing a year or episode number with an orange diamond",
                    "mark_filenames_missing_year_or_episode",
                    c.mark_filenames_missing_year_or_episode)
        ttk.Label(appearance, text="Appearance changes apply immediately after you save.",
                  wraplength=520).grid(row=3, column=0, columnspan=3, sticky="w",
                                       padx=8, pady=(8, 6))

        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Open JSON (advanced)", command=self._open_json).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right")

    def _choose_folder(self, var: tk.StringVar) -> None:
        selected = filedialog.askdirectory(parent=self, title="Choose folder")
        if selected:
            var.set(selected)

    def _open_json(self) -> None:
        try:
            import os
            os.startfile(default_config_path())  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Cannot open configuration", str(exc), parent=self)

    def _test_encoder_tools(self) -> None:
        handbrake = resolve_program(str(self.vars["handbrake_cli"].get()).strip())
        ffprobe = resolve_program(str(self.vars["ffprobe"].get()).strip())
        lines = [
            f"HandBrakeCLI: {'Found — ' + handbrake if handbrake else 'Not Found'}",
            f"ffprobe: {'Found — ' + ffprobe if ffprobe else 'Not Found'}",
        ]
        self.tool_status.set("\n".join(lines))

    def _save(self) -> None:
        try:
            get = lambda name: self.vars[name].get()
            quality = {height: float(get(f"rf_{height}")) for height in ("2160", "1080", "720", "480")}
            codecs = tuple(x.strip().lower() for x in str(get("audio_passthrough_codecs")).split(",") if x.strip())
            updated = replace(
                self.config,
                input_directory=str(get("input_directory")).strip(), output_directory=str(get("output_directory")).strip(),
                temp_directory=str(get("temp_directory")).strip(), backup_originals=bool(get("backup_originals")),
                backup_suffix=str(get("backup_suffix")), preserve_metadata=bool(get("preserve_metadata")),
                encoder_preset=str(get("encoder_preset")), video_bit_depth=int(get("video_bit_depth")),
                crop_mode=str(get("crop_mode")), denoise_mode=str(get("denoise_mode")),
                hdr_denoise_mode=str(get("hdr_denoise_mode")), quality_rf_by_resolution=quality,
                always_reencode=self.config.always_reencode,
                minimum_projected_savings_percent=float(get("minimum_projected_savings_percent")),
                sample_count=int(get("sample_count")), sample_duration_seconds=float(get("sample_duration_seconds")),
                reanalysis_mode=("progressive" if str(get("reanalysis_mode")) ==
                                 "Progressively add one sample" else "new_set"),
                progressive_sample_max=int(get("progressive_sample_max")),
                duration_tolerance_seconds=float(get("duration_tolerance_seconds")),
                audio_stream_mode=str(get("audio_stream_mode")).strip().lower().replace(" ", "_"),
                audio_passthrough_codecs=codecs,
                audio_bitrate_per_channel=int(get("audio_bitrate_per_channel")),
                minimum_audio_bitrate=int(get("minimum_audio_bitrate")),
                watch_input_directory=bool(get("watch_input_directory")),
                file_ready_seconds=float(get("file_ready_seconds")),
                rescan_interval_seconds=float(get("rescan_interval_seconds")),
                handbrake_cli=str(get("handbrake_cli")).strip(), ffprobe=str(get("ffprobe")).strip(),
                mark_filenames_with_spaces=bool(get("mark_filenames_with_spaces")),
                mark_filenames_missing_year_or_episode=bool(
                    get("mark_filenames_missing_year_or_episode")),
                theme=str(get("theme")),
            )
            if not updated.input_directory or not updated.temp_directory:
                raise ValueError("Input and temporary folders cannot be blank")
            if not updated.backup_suffix:
                raise ValueError("Backup suffix cannot be blank")
            if updated.audio_bitrate_per_channel <= 0 or updated.minimum_audio_bitrate <= 0:
                raise ValueError("Audio bitrates must be greater than zero")
            save_config(updated)
        except (TypeError, ValueError) as exc:
            messagebox.showerror("Invalid setting", str(exc), parent=self)
            return
        self.parent.input_var.set(updated.input_directory)
        self.parent.set_active_scanning(updated.watch_input_directory, persist=False)
        self.parent.mark_filenames_with_spaces = updated.mark_filenames_with_spaces
        self.parent.mark_filenames_missing_year_or_episode = (
            updated.mark_filenames_missing_year_or_episode)
        self.parent._refresh_filename_markers()
        self.parent.apply_theme(updated.theme)
        messagebox.showinfo("Settings saved", "Settings were saved to config.json.\nThey will apply the next time you press Start.", parent=self)
        self.destroy()


class OptimizerGUI(tk.Frame):
    def __init__(self, master=None, clean_request_callback=None,
                 output_ready_callback=None, status_callback=None,
                 can_start_callback=None, rename_request_callback=None,
                 rename_directory_callback=None,
                 inventory_state_callback=None) -> None:
        super().__init__(master)
        self.clean_request_callback = clean_request_callback
        self.clean_available = clean_request_callback is not None
        self.output_ready_callback = output_ready_callback
        self.status_callback = status_callback
        self.can_start_callback = can_start_callback
        self.rename_request_callback = rename_request_callback
        self.rename_directory_callback = rename_directory_callback
        self.inventory_state_callback = inventory_state_callback
        logging.basicConfig(
            filename=application_directory() / "MKVEncoderQ.log",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
        self.events: queue.Queue[tuple] = queue.Queue()
        # Active encode feedback must not wait behind thousands of directory
        # inventory or inspection updates.
        self.urgent_events: queue.Queue[tuple] = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.current_cancel_event: threading.Event | None = None
        self.running = False
        self.last_file_completed = False
        self.start_pending = False
        self.idle_discovery_quiet = False
        self.worker: threading.Thread | None = None
        self.watcher: threading.Thread | None = None
        self.encode_active = threading.Event()
        self.scan_enabled = threading.Event()
        self.scan_cycle_complete = threading.Event()
        self.scan_now_event = threading.Event()
        self.reanalyze_target: Path | None = None
        self.reanalyze_preserve: set[Path] = set()
        self.queued_reanalysis: dict[Path, tuple[list, int]] = {}
        self.sample_variants: dict[Path, int] = {}
        self.progressive_samples: dict[Path, tuple[tuple, tuple]] = {}
        self.progressive_targets: dict[Path, int] = {}
        self.queued_force: dict[Path, tuple[list, int]] = {}
        self.force_encode_paths: set[Path] = set()
        self.clean_paths: set[Path] = set()
        self.completed_outputs: dict[Path, Path] = {}
        self.retained_failed_outputs: dict[Path, Path] = {}
        self.encode_failure_details: dict[Path, str] = {}
        self.deleted_backups: set[Path] = set()
        self.missing_row_values: dict[Path, list] = {}
        self.file_selection: dict[Path, bool] = {}
        self.dropped_entries: dict[Path, Path] = {}
        self.drag_iid: str | None = None
        self.live: LiveQueue | None = None
        self.idle_discovery: threading.Thread | None = None
        self.last_idle_scan_started = 0.0
        self.last_idle_scan_duration = 0.0
        self.scan_configured_interval = 5.0
        self.scan_next_deadline = 0.0
        self.scan_last_display: str | None = None
        self.last_scan_status_refresh = 0.0
        self.manual_scan_requested = False
        self.last_idle_scan_root: Path | None = None
        self.directory_discovery_active = False
        self.directory_discovered_count = 0
        self.directory_scan_baseline_count = 0
        self.discovery_batch_backpressure = True
        self.directory_discovery_cancel = threading.Event()
        self.inspection_inventory_gate = threading.Event()
        self.inspection_inventory_gate.set()
        self.inventory_folders_scanned = 0
        self.inventory_files_scanned = 0
        self.inventory_top_level_folders = 0
        self.global_refresh_pending = False
        self.background_ui = False
        self.missing_refresh_pending = False
        self.last_global_refresh = 0.0
        self.inspection_thread: threading.Thread | None = None
        self.inspection_generation = 0
        self.inspection_cycle_active = False
        self.inspection_cancel = threading.Event()
        self.inspection_queue: queue.Queue[Path] = queue.Queue()
        self.inspection_lock = threading.RLock()
        self.inspection_enqueued: set[Path] = set()
        self.inspection_active_path: Path | None = None
        self.inspection_total = 0
        self.inspection_done = 0
        self.inspection_failed = 0
        self.inspection_started_at = 0.0
        self.inspection_results: dict[Path, MediaInspection] = {}
        self.inspection_errors: dict[Path, str] = {}
        self.inspection_cache = InspectionCache()
        self.inspection_batch: list[Path] = []
        self.filtered_rows: set[str] = set()
        self.filter_auto_excluded: set[Path] = set()
        self.filter_order: list[str] = []
        self.manual_order_active = False
        try:
            initial_config = load_config(None)
        except Exception:
            initial_config = Config()
        self.default_encode_selected = initial_config.select_new_files_for_encoding
        self.inspect_new_files = initial_config.inspect_new_files
        self.clean_new_encodes = initial_config.clean_new_encodes
        self.mark_filenames_with_spaces = initial_config.mark_filenames_with_spaces
        self.mark_filenames_missing_year_or_episode = (
            initial_config.mark_filenames_missing_year_or_episode)
        self.initial_config = initial_config
        self.scan_configured_interval = initial_config.rescan_interval_seconds
        if initial_config.watch_input_directory:
            self.scan_enabled.set()
        self.style = ttk.Style(self)
        self.theme_name = initial_config.theme
        self.apply_theme(initial_config.theme)
        self._build()
        self.set_clean_available(self.clean_available)
        self.after(100, self._drain_events)
        self.after(150, self._idle_scan_tick)

    def _build(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Input folder:").grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar(value=self.initial_config.input_directory)
        ttk.Entry(top, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Browse...", command=self._browse).grid(row=0, column=2)
        self.start_button = ttk.Button(
            top, text="▶ Start", command=self.start, style="StartIdle.TButton")
        self.start_button.grid(row=0, column=3, padx=(12, 4))
        self.stop_button = ttk.Button(
            top, text="■ Stop All", command=self.stop, state="disabled",
            style="StopIdle.TButton")
        self.stop_button.grid(row=0, column=4)
        self.pause_button = ttk.Button(top, text="⏸ Pause", command=self.toggle_pause, state="disabled")
        self.pause_button.grid(row=0, column=5, padx=(4, 0))
        self.cancel_button = ttk.Button(top, text="✖ Cancel File", style="Cancel.TButton", command=self.cancel_file, state="disabled")
        self.cancel_button.grid(row=0, column=6, padx=(4, 0))
        self.control_indicator = ttk.Label(
            top, text="●", style="RunningIndicator.TLabel", anchor="center")
        self.control_indicator.grid(row=1, column=3, sticky="ew")
        self.control_indicator.grid_remove()
        ttk.Button(top, text="Settings", command=self._settings).grid(row=0, column=7, padx=(4, 0))
        ttk.Button(top, text="User Guide", command=self._open_user_guide).grid(
            row=0, column=8, padx=(4, 0))
        self.active_scan_var = tk.BooleanVar(value=self.scan_enabled.is_set())
        scan_controls = ttk.Frame(top)
        scan_controls.grid(row=2, column=0, columnspan=9, sticky="w", pady=(8, 0))
        ttk.Label(scan_controls, text="Active Directory Scanning",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        self.active_scan_check = DirectoryToggle(
            scan_controls, self.scan_enabled.is_set(), self.set_active_scanning)
        self.active_scan_check.pack(side="left", padx=(7, 10))
        self.scan_now_button = ttk.Button(
            scan_controls, text="Scan Now", command=self.scan_now,
            state="normal" if self.scan_enabled.is_set() else "disabled")
        self.scan_now_button.pack(side="left", padx=(0, 10))
        self.scan_status_var = tk.StringVar(
            value=_scan_status_text(self.scan_enabled.is_set(),
                                    session_running=False))
        self.scan_status_label = ttk.Label(
            scan_controls, textvariable=self.scan_status_var,
            style="ScanOn.TLabel" if self.scan_enabled.is_set() else "ScanOff.TLabel",
            font=("Segoe UI", 9), padding=(2, 0),
        )
        self.scan_status_label.pack(side="left", padx=2)
        top.columnconfigure(1, weight=1)

        files_row = ttk.Frame(self, padding=(10, 8, 10, 4))
        files_row.pack(fill="x")
        self.files_count_var = tk.StringVar(value=_file_count_text(0, 0, 0))
        ttk.Label(files_row, textvariable=self.files_count_var,
                  font=("Segoe UI", 11, "bold"), width=50,
                  anchor="w").pack(side="left")
        ttk.Label(files_row, text="New files selected",
                  font=("Segoe UI", 9, "bold")).pack(side="left", padx=(24, 4))
        self.default_encode_toggle = DirectoryToggle(
            files_row, self.default_encode_selected, self.set_default_selection)
        self.default_encode_toggle.pack(side="left", padx=4)
        ttk.Label(files_row, text="Inspect files when added",
                  font=("Segoe UI", 9, "bold")).pack(
            side="left", padx=(24, 4))
        self.inspect_new_toggle = DirectoryToggle(
            files_row, self.inspect_new_files, self.set_inspect_new_files)
        self.inspect_new_toggle.pack(side="left", padx=4)
        ttk.Label(files_row, text="Clean After Encode",
                  font=("Segoe UI", 9, "bold")).pack(
            side="left", padx=(24, 4))
        self.clean_new_toggle = DirectoryToggle(
            files_row, self.clean_new_encodes, self.set_clean_new_encodes)
        self.clean_new_toggle.pack(side="left", padx=4)
        file_actions = ttk.Frame(files_row)
        file_actions.pack(side="right", padx=(0, 6))
        self.home_button = ttk.Button(
            file_actions, text="Home", command=self.go_home)
        self.home_button.pack(fill="x", pady=(0, 4))
        self.remove_missing_button = ttk.Button(
            file_actions, text="Remove Missing Files",
            command=self.remove_missing_files, state="disabled")
        self.remove_missing_button.pack(fill="x")

        inventory_row = ttk.Frame(self, padding=(10, 0, 10, 5))
        inventory_row.pack(fill="x")
        self.inventory_status_var = tk.StringVar(
            value="Waiting for initial scan")
        ttk.Label(inventory_row, text="Directory inventory:",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(inventory_row, textvariable=self.inventory_status_var).pack(
            side="left", padx=(5, 10), fill="x", expand=True)
        self.inventory_progress = ttk.Progressbar(
            inventory_row, mode="determinate", maximum=100, value=0,
            length=220, style="Inventory.Horizontal.TProgressbar")
        self.inventory_progress.pack(side="left", padx=(0, 8))
        self.cancel_inventory_button = ttk.Button(
            inventory_row, text="Cancel Inventory",
            command=self.cancel_directory_inventory, state="disabled")
        self.cancel_inventory_button.pack(side="right")

        inspect_row = ttk.Frame(self, padding=(10, 0, 10, 5))
        inspect_row.pack(fill="x")
        self.inspection_status_var = tk.StringVar(
            value=_inspection_summary(0, 0, 0, "complete"))
        ttk.Label(inspect_row, text="Inspection:",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(inspect_row, textvariable=self.inspection_status_var).pack(
            side="left", padx=(5, 10), fill="x", expand=True)
        self.inspection_progress = ttk.Progressbar(
            inspect_row, mode="determinate", maximum=100, value=0, length=220,
            style="Inspection.Horizontal.TProgressbar")
        self.inspection_progress.pack(side="left", padx=(0, 8))
        self.cancel_inspection_button = ttk.Button(
            inspect_row, text="Cancel Inspection", command=self.cancel_inspection,
            state="disabled")
        self.cancel_inspection_button.pack(side="right")

        filter_row = ttk.Frame(self, padding=(10, 0, 10, 5))
        filter_row.pack(fill="x")
        self.codec_filter_var = tk.StringVar(value="All")
        self.resolution_filter_var = tk.StringVar(value="All")
        self.rating_filter_var = tk.StringVar(value="All")
        self.sort_mode_var = tk.StringVar(value="Largest First")
        self.filter_widgets = []
        for label, variable, choices in (
                ("Codec", self.codec_filter_var,
                 ("All", "H.264", "H.265 / HEVC", "AV1", "Other")),
                ("Resolution", self.resolution_filter_var,
                 ("All", "SD", "720p", "1080p", "2160p / 4K", "1080p or Lower")),
                ("Rating", self.rating_filter_var,
                 ("All", "Wasteful", "Review Size", "Unknown", "OK")),
                ("Sort", self.sort_mode_var,
                 ("Largest First", "Smallest First", "Most Wasteful", "Filename"))):
            ttk.Label(filter_row, text=f"{label}:").pack(side="left", padx=(0, 3))
            widget = ttk.Combobox(
                filter_row, textvariable=variable, values=choices,
                state="readonly", width=17)
            widget.pack(side="left", padx=(0, 10))
            widget.bind(
                "<<ComboboxSelected>>",
                lambda _event, sort_changed=(label == "Sort"):
                    self._media_view_option_changed(sort_changed))
            self.filter_widgets.append(widget)
        self.filter_note_var = tk.StringVar(value="Filters available before Start")
        self.filter_note_label = ttk.Label(
            filter_row, textvariable=self.filter_note_var,
            style="FilterReady.TLabel", font=("Segoe UI", 9, "italic"))
        self.filter_note_label.pack(side="right", padx=(8, 0))

        columns = ("handle", "force", "file", "status", "progress", "savings",
                   "current_size", "projected", "decision", "action", "selected", "path",
                   "clean", "media", "rating", "backup")
        self.table = ttk.Treeview(self, columns=columns, show="headings", height=16)
        self.table.configure(displaycolumns=ENCODER_DISPLAY_COLUMNS)
        for key, label, width in (("handle", "", 34),
                                  ("force", _force_all_text([]), 100),
                                  ("clean", _clean_all_text([]), 100),
                                  ("file", "File", 240), ("path", "Path", 300),
                                  ("media", "Media", 135), ("rating", "Rating", 90),
                                  ("status", "Status", 105),
                                  ("progress", "Progress", 80), ("savings", "Savings", 72),
                                  ("current_size", "Source size", 84),
                                  ("projected", "Est. output", 84),
                                  ("decision", "Decision / result", 250),
                                  ("backup", "Backup", 90),
                                  ("action", "Action", 120),
                                  ("selected", _encode_all_text([]), 110)):
            if key == "force":
                self.table.heading(
                    key, text=label, command=self.toggle_all_force_encode)
            elif key == "clean":
                self.table.heading(
                    key, text=label, command=self.toggle_all_clean)
            elif key == "selected":
                self.table.heading(
                    key, text=label, command=self.toggle_all_file_selection)
            else:
                self.table.heading(key, text=label)
            self.table.column(
                key, width=width, minwidth=width if key in {
                    "handle", "force", "selected", "clean"} else 20,
                # Only genuinely variable text benefits from spare width.
                # Predictable status/size/action fields stay compact instead
                # of growing while Decision / result remains truncated.
                stretch=key in {"file", "path", "decision"},
                anchor="w" if key in {"file", "path"} else "center")
        self.table.bind("<ButtonPress-1>", self._table_press)
        self.table.bind("<ButtonRelease-1>", self._table_click)
        self.table.bind("<B1-Motion>", self._table_drag)
        self.table.bind("<Motion>", self._table_motion)
        self.table.bind("<Button-3>", self._show_file_menu)
        self.table.bind("<Shift-F10>", self._show_selected_file_menu)
        self.table.bind("<Control-a>", self._select_all_rows)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(fill="both", expand=True, padx=(10, 0))
        scroll.place(relx=1.0, rely=0.09, relheight=0.55, anchor="ne")

        detail = ttk.LabelFrame(self, text="Current file / details", padding=10)
        detail.pack(fill="both", expand=True, padx=10, pady=10)
        self.current_var = tk.StringVar(value="Ready. Choose an input folder and press Start.")
        ttk.Label(detail, textvariable=self.current_var,
                  font=("Segoe UI", 14, "bold")).pack(anchor="w")
        self.encode_stats_var = tk.StringVar(value="")
        ttk.Label(detail, textvariable=self.encode_stats_var,
                  font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))
        self.settings_var = tk.StringVar(value="")
        ttk.Label(detail, textvariable=self.settings_var,
                  font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))
        self.projection_var = tk.StringVar(value="")
        ttk.Label(detail, textvariable=self.projection_var).pack(anchor="w", pady=(2, 0))
        self.progress = LabeledProgress(detail)
        self.progress.pack(fill="x", pady=(8, 8))
        self.log = tk.Text(detail, height=7, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w").pack(fill="x", side="bottom")
        self.apply_theme(self.theme_name)

    def apply_theme(self, theme: str) -> None:
        self.theme_name = theme if theme in {"light", "dark"} else "light"
        dark = self.theme_name == "dark"
        colors = ({
            "bg": "#1e1f22", "panel": "#27292d", "field": "#303238",
            "fg": "#f2f2f2", "muted": "#c7c9ce", "accent": "#4f9cf9",
            "select": "#365f91", "border": "#454850", "bar_text": "#ffffff",
        } if dark else {
            "bg": "#f0f0f0", "panel": "#ffffff", "field": "#ffffff",
            "fg": "#202124", "muted": "#404246", "accent": "#2678d4",
            "select": "#cde4ff", "border": "#b8b8b8", "bar_text": "#101010",
        })
        self.theme_colors = colors
        self.configure(background=colors["bg"])
        self.style.theme_use("clam")
        self.style.configure(".", background=colors["bg"], foreground=colors["fg"],
                             fieldbackground=colors["field"])
        self.style.configure("TFrame", background=colors["bg"])
        self.style.configure("TLabelframe", background=colors["bg"],
                             foreground=colors["fg"], bordercolor=colors["border"])
        self.style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["fg"])
        self.style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
        self.style.configure("TCheckbutton", background=colors["bg"], foreground=colors["fg"])
        self.style.map(
            "TCheckbutton",
            background=[("active", colors["bg"]), ("pressed", colors["bg"])],
            foreground=[("active", colors["fg"]), ("pressed", colors["fg"]),
                        ("disabled", colors["muted"])],
        )
        self.style.configure("TButton", background=colors["panel"], foreground=colors["fg"],
                             bordercolor=colors["border"], padding=5)
        self.style.map("TButton", background=[("active", colors["select"]),
                                               ("disabled", colors["bg"])],
                       foreground=[("disabled", colors["muted"])])
        self.style.configure(
            "StartIdle.TButton", background=colors["panel"], foreground=colors["fg"],
            bordercolor=colors["border"], padding=5)
        self.style.map(
            "StartIdle.TButton",
            background=[("active", colors["select"]), ("disabled", colors["bg"])],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.configure(
            "StartActive.TButton", background="#198754", foreground="#ffffff",
            bordercolor="#12653f", padding=5)
        self.style.map(
            "StartActive.TButton",
            background=[("active", "#157347"), ("disabled", "#198754")],
            foreground=[("disabled", "#ffffff")],
        )
        self.style.configure(
            "StopIdle.TButton", background=colors["panel"], foreground=colors["fg"],
            bordercolor=colors["border"], padding=5)
        self.style.map(
            "StopIdle.TButton",
            background=[("active", colors["select"]), ("disabled", colors["bg"])],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.configure(
            "StopPending.TButton", background="#b3261e", foreground="#ffffff",
            bordercolor="#861d18", padding=5)
        self.style.map(
            "StopPending.TButton",
            background=[("active", "#9c211b"), ("disabled", "#b3261e")],
            foreground=[("disabled", "#ffffff")],
        )
        self.style.configure(
            "RunningIndicator.TLabel", background=colors["bg"],
            foreground="#24b36b", font=("Segoe UI", 10, "bold"))
        self.style.configure(
            "StoppingIndicator.TLabel", background=colors["bg"],
            foreground="#e23b32", font=("Segoe UI", 10, "bold"))
        self.style.configure(
            "PausedIndicator.TLabel", background=colors["bg"],
            foreground="#e6b450", font=("Segoe UI", 10, "bold"))
        self.style.configure(
            "CancellingIndicator.TLabel", background=colors["bg"],
            foreground="#ff4b44", font=("Segoe UI", 10, "bold"))
        self.style.configure("TEntry", fieldbackground=colors["field"], foreground=colors["fg"],
                             insertcolor=colors["fg"], bordercolor=colors["border"])
        self.style.configure("TCombobox", fieldbackground=colors["field"], foreground=colors["fg"],
                             selectbackground=colors["select"], selectforeground=colors["fg"],
                             arrowcolor=colors["fg"], bordercolor=colors["border"])
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["field"]),
                             ("disabled", colors["bg"])],
            foreground=[("readonly", colors["fg"]),
                        ("disabled", colors["muted"])],
            selectbackground=[("readonly", colors["field"])],
            selectforeground=[("readonly", colors["fg"])],
            arrowcolor=[("readonly", colors["fg"]),
                        ("disabled", colors["muted"])],
        )
        # The opened combobox list is a classic Tk Listbox, not a ttk widget,
        # so it does not inherit the TCombobox style above.
        self.option_add("*TCombobox*Listbox.background", colors["field"])
        self.option_add("*TCombobox*Listbox.foreground", colors["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", colors["select"])
        self.option_add("*TCombobox*Listbox.selectForeground", colors["fg"])
        self.style.configure("TNotebook", background=colors["bg"], bordercolor=colors["border"])
        self.style.configure("TNotebook.Tab", background=colors["panel"], foreground=colors["fg"])
        self.style.map("TNotebook.Tab", background=[("selected", colors["select"])])
        self.style.configure("Treeview", background=colors["field"], fieldbackground=colors["field"],
                             foreground=colors["fg"], bordercolor=colors["border"], rowheight=24)
        self.style.configure("Treeview.Heading", background=colors["panel"], foreground=colors["fg"])
        self.style.map(
            "Treeview.Heading",
            background=[("active", colors["select"]),
                        ("pressed", colors["select"])],
            foreground=[("active", colors["fg"]),
                        ("pressed", colors["fg"])],
        )
        self.style.map("Treeview", background=[("selected", colors["select"])],
                       foreground=[("selected", colors["fg"])])
        if hasattr(self, "table"):
            self.table.tag_configure("missing", foreground=colors["muted"])
            self.table.tag_configure(
                "sampling", background="#183f66" if dark else "#d8ebff",
                foreground=colors["fg"], font=("Segoe UI", 9, "bold"))
            self.table.tag_configure(
                "encoding", background="#185238" if dark else "#d5f3df",
                foreground=colors["fg"], font=("Segoe UI", 9, "bold"))
            self.table.tag_configure(
                "paused", background="#66501a" if dark else "#fff0c2",
                foreground=colors["fg"], font=("Segoe UI", 9, "bold"))
            self.table.tag_configure(
                "larger_output", foreground="#ffd166" if dark else "#9a6700",
                font=("Segoe UI", 9, "bold"))
        self.style.configure("Main.Horizontal.TProgressbar", troughcolor=colors["field"],
                             background=colors["accent"], bordercolor=colors["border"], thickness=22)
        self.style.configure(
            "Inspection.Horizontal.TProgressbar",
            troughcolor=colors["panel"], background="#2f80c9",
            bordercolor=colors["border"], lightcolor="#2f80c9",
            darkcolor="#2f80c9", thickness=12,
        )
        self.style.configure(
            "Inventory.Horizontal.TProgressbar",
            troughcolor=colors["panel"], background="#c98224",
            bordercolor=colors["border"], lightcolor="#c98224",
            darkcolor="#c98224", thickness=12,
        )
        self.style.configure("ProgressContainer.TFrame", background=colors["field"])
        self.style.configure("ProgressText.TLabel", background=colors["field"],
                             foreground=colors["bar_text"], padding=(4, 0))
        self.style.configure("ScanOn.TLabel", background=colors["bg"],
                             foreground="#2f9d58")
        self.style.configure("ScanOff.TLabel", background=colors["bg"],
                             foreground=colors["muted"])
        self.style.configure("FilterReady.TLabel", background=colors["bg"],
                             foreground=colors["muted"])
        self.style.configure("FilterLocked.TLabel", background=colors["bg"],
                             foreground="#c98224" if dark else "#8a5400")
        self.style.configure("Cancel.TButton", foreground="#d32f2f",
                             font=("Segoe UI", 11, "bold"))
        if hasattr(self, "active_scan_check"):
            self.active_scan_check.configure(background=colors["bg"])
        if hasattr(self, "default_encode_toggle"):
            self.default_encode_toggle.configure(background=colors["bg"])
        if hasattr(self, "inspect_new_toggle"):
            self.inspect_new_toggle.configure(background=colors["bg"])
        if hasattr(self, "clean_new_toggle"):
            self.clean_new_toggle.configure(background=colors["bg"])
        if hasattr(self, "log"):
            self.log.configure(background=colors["field"], foreground=colors["fg"],
                               insertbackground=colors["fg"], selectbackground=colors["select"])

    def _show_scan_status(self, enabled: bool, scanning: bool = False,
                          last_scan: str | None = None) -> None:
        if last_scan is not None:
            self.scan_last_display = last_scan
        count = max(
            getattr(self, "directory_discovered_count", 0),
            getattr(self, "inventory_files_scanned", 0),
            getattr(self, "inventory_folders_scanned", 0))
        deadline = getattr(self, "scan_next_deadline", 0.0)
        remaining = (max(0.0, deadline - time.monotonic())
                     if enabled and deadline and not scanning else None)
        self.scan_status_var.set(_scan_status_text(
            enabled, scanning, getattr(self, "scan_last_display", None),
            session_running=self.running, item_count=count,
            next_scan_seconds=remaining,
            encoding=getattr(self, "encode_active", threading.Event()).is_set()))
        self.scan_status_label.configure(
            style="ScanOn.TLabel" if enabled else "ScanOff.TLabel")
        if hasattr(self, "scan_now_button"):
            self.scan_now_button.configure(
                state=("disabled" if not enabled or scanning or getattr(
                    self, "directory_discovery_active", False) else "normal"))

    def scan_now(self) -> None:
        """Request one immediate non-overlapping directory scan."""
        if (not self.scan_enabled.is_set()
                or getattr(self, "directory_discovery_active", False)):
            return
        self.scan_next_deadline = 0.0
        if self.running:
            self.scan_now_event.set()
            self.scan_status_var.set("● Manual directory scan requested…")
            self.scan_now_button.configure(state="disabled")
        else:
            self.manual_scan_requested = True
            self.last_idle_scan_started = 0.0
            self.scan_now_button.configure(state="disabled")
            self._idle_scan_tick(schedule_next=False)

    def set_background_ui(self, background: bool) -> None:
        """Reduce bulk Encoder painting while another application tab is open."""
        self.background_ui = background

    def _set_filter_lock_state(self, locked: bool) -> None:
        for widget in self.filter_widgets:
            widget.configure(state="disabled" if locked else "readonly")
        self.filter_note_var.set(
            "🔒 Filters locked during processing"
            if locked else "Filters available before Start")
        self.filter_note_label.configure(
            style="FilterLocked.TLabel" if locked else "FilterReady.TLabel")

    def set_active_scanning(self, enabled: bool, persist: bool = True) -> None:
        self.active_scan_var.set(enabled)
        self.active_scan_check.set(enabled)
        if enabled:
            self.scan_enabled.set()
        else:
            self.scan_enabled.clear()
        self._show_scan_status(enabled)
        if self.running:
            state = "enabled" if enabled else "disabled"
            self._log(f"Active Directory Scanning {state}.")
            if (enabled and not self.stop_event.is_set()
                    and (self.watcher is None or not self.watcher.is_alive())):
                self.watcher = threading.Thread(
                    target=self._watch, args=(self.run_config,), daemon=True)
                self.watcher.start()
        if persist:
            try:
                config = load_config(None)
                save_config(replace(config, watch_input_directory=enabled))
            except (OSError, ValueError) as exc:
                self._log(f"Could not save the active-scanning setting: {exc}")

    def toggle_active_scanning(self) -> None:
        self.set_active_scanning(self.active_scan_var.get())

    def set_default_selection(self, selected: bool, persist: bool = True) -> None:
        self.default_encode_selected = selected
        self.default_encode_toggle.set(selected)
        if getattr(self, "live", None) is not None:
            self.live.default_selected = selected
        if persist:
            try:
                config = load_config(None)
                save_config(replace(config, select_new_files_for_encoding=selected))
            except (OSError, ValueError) as exc:
                self._log(f"Could not save the new-file selection setting: {exc}")

    def set_inspect_new_files(self, enabled: bool, persist: bool = True) -> None:
        self.inspect_new_files = enabled
        self.inspect_new_toggle.set(enabled)
        if persist:
            try:
                config = load_config(None)
                save_config(replace(config, inspect_new_files=enabled))
            except (OSError, ValueError) as exc:
                self._log(f"Could not save the inspection setting: {exc}")
        if enabled and persist and getattr(self, "directory_discovery_active", False):
            self.inspection_status_var.set(
                "Waiting for directory inventory to finish before inspection")
        elif enabled and persist:
            pending = []
            for iid in self._all_table_rows():
                path = Path(iid)
                if self.live is None or path not in self.live.items:
                    path = path.resolve()
                if path not in self.inspection_results:
                    pending.append(path)
            if pending and messagebox.askyesno(
                    "Inspect files already listed?",
                    f"Inspect the {len(pending)} file(s) already in the Encoder list?\n\n"
                    "This reads media information only; it does not sample or encode.",
                    parent=self):
                self.queue_inspection(pending)

    def set_clean_new_encodes(self, enabled: bool, persist: bool = True) -> None:
        """Set the Cleaner default for files discovered after this change."""
        self.clean_new_encodes = enabled
        self.clean_new_toggle.set(enabled)
        if persist:
            try:
                config = load_config(None)
                save_config(replace(config, clean_new_encodes=enabled))
            except (OSError, ValueError) as exc:
                self._log(f"Could not save the Clean After Encode setting: {exc}")

    def queue_inspection(self, paths) -> int:
        """Queue uncached media inspections without blocking the interface."""
        cycle_was_active = getattr(self, "inspection_cycle_active", False)
        queued = 0
        for raw_path in paths:
            path = Path(raw_path)
            # Inventory and table paths are already canonical.  Resolving each
            # UNC path again can perform another NAS lookup and freeze Tk while
            # thousands of inspection jobs are prepared.
            if self.live is None or path not in self.live.items:
                path = path.resolve()
            if path in self.inspection_results or path in self.inspection_errors:
                continue
            with self.inspection_lock:
                if path in self.inspection_enqueued:
                    continue
                self.inspection_enqueued.add(path)
                self.inspection_total += 1
            self.inspection_queue.put(path)
            queued += 1
        if queued:
            self.inspection_cycle_active = True
            if self.inspection_total == queued:
                self.inspection_started_at = time.monotonic()
            self.cancel_inspection_button.configure(state="normal")
            # An active cycle owns worker creation through its final GUI
            # completion event.  New paths join that cycle; if its worker has
            # just exited, inspection_finished starts the next drain.  This
            # prevents overlapping workers and out-of-order finish events.
            if not cycle_was_active:
                self.inspection_cancel.clear()
                self.inspection_thread = threading.Thread(
                    target=self._inspection_worker,
                    args=(getattr(self, "inspection_generation", 0),),
                    daemon=True)
                self.inspection_thread.start()
        return queued

    def _inspection_worker(self, generation: int | None = None) -> None:
        generation = (getattr(self, "inspection_generation", 0)
                      if generation is None else generation)
        try:
            ffprobe = resolve_program(load_config(None).ffprobe)
        except Exception:
            ffprobe = None
        while (generation == getattr(self, "inspection_generation", 0)
               and not self.inspection_cancel.is_set()):
            # Inventory and ffprobe never compete for a large NAS tree.  The
            # inventory fixes the denominator before inspection consumes work.
            while not self.inspection_inventory_gate.wait(0.1):
                if self.inspection_cancel.is_set():
                    break
            if self.inspection_cancel.is_set():
                break
            with self.inspection_lock:
                try:
                    path = self.inspection_queue.get_nowait()
                except queue.Empty:
                    break
                self.inspection_active_path = path
            error = ""
            result = None
            try:
                result = self.inspection_cache.get(path)
                if result is None:
                    if not ffprobe:
                        raise RuntimeError("ffprobe was not found")
                    result = inspect_media(path, ffprobe)
                    self.inspection_cache.put(result, persist=False)
            except Exception as exc:
                error = str(exc)
            with self.inspection_lock:
                if generation != getattr(self, "inspection_generation", 0):
                    return
                self.inspection_active_path = None
                self.inspection_done += 1
                if error:
                    self.inspection_failed += 1
                done, total, failed = (
                    self.inspection_done, self.inspection_total,
                    self.inspection_failed)
            self._emit("inspection_result", path, result, error, done, total, failed)
        self.inspection_cache.save()
        if generation != getattr(self, "inspection_generation", 0):
            return
        cancelled = self.inspection_cancel.is_set()
        if cancelled:
            with self.inspection_lock:
                while True:
                    try:
                        path = self.inspection_queue.get_nowait()
                    except queue.Empty:
                        break
                    self.inspection_enqueued.discard(path)
        self._emit("inspection_finished", cancelled)

    def cancel_inspection(self) -> None:
        self.inspection_cancel.set()
        self.cancel_inspection_button.configure(state="disabled")
        self.inspection_status_var.set("Cancelling media inspection...")

    def _finish_inspection_cycle(self, cancelled: bool) -> None:
        """Complete one worker drain and release Start only after GUI delivery."""
        done = len(self.inspection_results) + len(self.inspection_errors)
        failed = len(self.inspection_errors)
        total = max(getattr(self, "directory_discovered_count", 0), done)
        self.cancel_inspection_button.configure(state="disabled")
        self.inspection_progress["value"] = (done / total * 100
                                               if total else 0)
        with self.inspection_lock:
            queued_work = not self.inspection_queue.empty()
            unsettled = (queued_work
                         or self.inspection_active_path is not None
                         or bool(self.inspection_enqueued))
        if not cancelled and unsettled:
            self.inspection_status_var.set(
                "Inspection continuing — processing newly queued files…")
            # The worker that emitted inspection_finished has ended its
            # ownership of the queue. Start exactly one new drain for work
            # added during result delivery.
            if queued_work:
                self.inspection_thread = threading.Thread(
                    target=self._inspection_worker,
                    args=(getattr(self, "inspection_generation", 0),),
                    daemon=True)
                self.inspection_thread.start()
            return

        if cancelled:
            self.inspection_status_var.set(
                _inspection_summary(
                    getattr(self, "directory_discovered_count", total),
                    done, failed, "cancelled"))
        else:
            self.inspection_status_var.set(
                "Inspection finished — applying filters…")
        if not self.running:
            self.apply_media_view()
        self.inspection_cycle_active = False
        if not cancelled:
            self.inspection_status_var.set(
                _inspection_summary(total, done, failed, "complete"))
            if self.start_pending:
                self.after_idle(self._continue_pending_start)
        else:
            with self.inspection_lock:
                self.inspection_total = 0
                self.inspection_done = 0
                self.inspection_failed = 0

    def cancel_directory_inventory(self) -> None:
        """Stop the background directory walk without touching queue files."""
        if not getattr(self, "directory_discovery_active", False):
            return
        self.directory_discovery_cancel.set()
        self.cancel_inventory_button.configure(state="disabled")
        self.inventory_status_var.set("Cancelling directory inventory…")

    def _update_row_media(self, path: Path, result: MediaInspection | None,
                          error: str = "") -> None:
        path = Path(path)
        iid = str(path if self.live is not None and path in self.live.items
                  else path.resolve())
        if not self.table.exists(iid):
            return
        values = list(self.table.item(iid, "values"))
        while len(values) < 15:
            values.append("—")
        values[13] = result.media_text if result else "Inspection failed"
        values[14] = result.rating if result else "ERROR"
        status = str(values[3])
        if status == "Not selected" and len(values) > 8:
            values[8] = "Excluded from processing"
        elif result is not None and len(values) > 8 and status not in {
                "Sampling", "Encoding", "Paused", "Completed", "Skipped",
                "Failed", "Cancelled", "Reverted", "Missing"}:
            values[8] = "Media inspected — Ready"
        if error and len(values) > 8 and status not in {
                "Sampling", "Encoding", "Paused", "Completed", "Skipped",
                "Failed", "Cancelled", "Reverted", "Missing"}:
            values[8] = f"Inspection failed: {error}"
        self.table.item(iid, values=values)

    def _all_table_rows(self) -> list[str]:
        if not self.filter_order:
            rows = list(self.table.get_children())
            known = set(rows)
            rows.extend(iid for iid in getattr(self, "filtered_rows", ())
                        if iid not in known)
            return rows
        # filter_order is maintained whenever rows are removed or renamed.
        # Calling Treeview.exists once per file makes every checkbox click and
        # Start operation scale with the complete multi-thousand-row library.
        known = list(self.filter_order)
        known_set = set(known)
        filtered = getattr(self, "filtered_rows", ())
        known.extend(iid for iid in filtered if iid not in known_set)
        known_set.update(filtered)
        known.extend(iid for iid in self.table.get_children() if iid not in known_set)
        return known

    def _refresh_filtered_row_selection(self, path: Path, item, values: list) -> None:
        """Keep checkbox, status, and decision synchronized after filtering."""
        values[1] = _force_text(item.force_encode)
        values[3] = item.status
        error = self.inspection_errors.get(path)
        if error:
            values[8] = f"Inspection failed: {error}"
        elif not item.selected:
            values[8] = "Excluded from processing"
        elif path in self.inspection_results:
            values[8] = "Media inspected — Ready"
        else:
            values[8] = item.detail
        values[10] = _encode_text(item.selected)

    def _path_matches_active_filter(self, path: Path) -> bool:
        """Return whether one inventory record belongs in the active view."""
        codec_filter = self.codec_filter_var.get()
        resolution_filter = self.resolution_filter_var.get()
        rating_filter = self.rating_filter_var.get()
        if codec_filter == resolution_filter == rating_filter == "All":
            return True
        result = self.inspection_results.get(Path(path))
        return result is not None and inspection_matches(
            result, codec_filter, resolution_filter, rating_filter)

    def _set_path_filter_visibility(self, path: Path, visible: bool,
                                    reattach_at_end: bool = True) -> None:
        """Apply one view transition without changing inventory ownership."""
        path = Path(path)
        iid = str(path)
        item = self.live.items.get(path) if self.live is not None else None
        if item is not None and _can_select(item.status):
            if not visible and item.selected:
                self.live.set_selected(path, False)
                self.file_selection[path] = False
                self.force_encode_paths.discard(path)
                self.filter_auto_excluded.add(path)
            elif (visible and path in self.filter_auto_excluded
                  and self.default_encode_selected):
                self.live.set_selected(path, True)
                self.file_selection[path] = True
                self.filter_auto_excluded.discard(path)
            values = list(self.table.item(iid, "values"))
            if len(values) >= 13:
                OptimizerGUI._refresh_filtered_row_selection(
                    self, path, item, values)
                self.table.item(iid, values=values)
        if visible:
            if reattach_at_end and iid in self.filtered_rows:
                self.table.move(iid, "", "end")
            self.filtered_rows.discard(iid)
        else:
            self.table.detach(iid)
            self.filtered_rows.add(iid)

    def apply_media_view(self) -> None:
        """Filter the inventory and keep hidden rows out of processing."""
        rows = self._all_table_rows()
        if not getattr(self, "manual_order_active", False):
            sort_mode = self.sort_mode_var.get()
            rows.sort(key=lambda iid: inspection_sort_key(
                self.inspection_results.get(Path(iid)), sort_mode,
                Path(iid).name))
        self.filter_order = rows
        self.filtered_rows.clear()
        for iid in rows:
            path = Path(iid)
            visible = OptimizerGUI._path_matches_active_filter(self, path)
            self.table.move(iid, "", "end")
            OptimizerGUI._set_path_filter_visibility(
                self, path, visible, reattach_at_end=False)
        self._refresh_encode_all_heading()
        self._refresh_force_all_heading()

    def _media_view_option_changed(self, sort_changed: bool = False) -> None:
        """Apply filters without discarding a deliberate handle order."""
        if sort_changed:
            self.manual_order_active = False
        self.apply_media_view()

    def _apply_filter_to_path(self, path: Path,
                              refresh_headings: bool = True) -> bool:
        """Apply the active filter to one newly inspected or discovered row."""
        path = Path(path)
        iid = str(path)
        if not self.table.exists(iid):
            return False
        # A row may arrive while a media-dependent filter is active and be
        # detached immediately.  Retain it in the authoritative order before
        # detaching so later filter changes can always find and reattach it.
        filter_order = getattr(self, "filter_order", None)
        if filter_order is not None and iid not in filter_order:
            filter_order.append(iid)
        visible = OptimizerGUI._path_matches_active_filter(self, path)
        OptimizerGUI._set_path_filter_visibility(self, path, visible)
        if refresh_headings:
            self._refresh_encode_all_heading()
            self._refresh_force_all_heading()
        return visible

    def _refresh_encode_all_heading(self) -> None:
        if getattr(self, "live", None) is not None:
            snapshot = self.live.snapshot()
            hidden = getattr(self, "filtered_rows", set())
            states = [item.selected for item in snapshot
                      if _can_select(item.status) and str(item.path) not in hidden]
            if hasattr(self, "files_count_var"):
                filtered = sum(
                    1 for item in snapshot if str(item.path) not in hidden)
                self.files_count_var.set(_file_count_text(
                    len(snapshot), filtered, sum(states)))
        else:
            states = []
            for iid in self._all_table_rows():
                values = self.table.item(iid, "values")
                if len(values) >= 13 and _can_select(str(values[3])):
                    states.append(str(values[10]) == _encode_text(True))
        self.table.heading("selected", text=_encode_all_text(states))

    def _refresh_force_all_heading(self) -> None:
        if self.live is not None:
            states = [
                item.force_encode or item.path in self.force_encode_paths
                for item in self.live.snapshot()
                if _can_force(item.status) or item.path in self.queued_force
            ]
        else:
            states = []
            for iid in self._all_table_rows():
                values = self.table.item(iid, "values")
                path = Path(iid)
                if len(values) >= 13 and (_can_force(str(values[3]))
                                          or path in self.queued_force):
                    states.append(path in self.force_encode_paths)
        self.table.heading("force", text=_force_all_text(states))

    def _refresh_clean_all_heading(self) -> None:
        if self.live is not None:
            paths = [item.path for item in self.live.snapshot()
                     if _can_clean(item.status)]
        else:
            paths = [Path(iid) for iid in self.table.get_children()
                     if _can_clean(str(self.table.set(iid, "status")))]
        self.table.heading(
            "clean", text=_clean_all_text(
                [path in self.clean_paths for path in paths], self.clean_available))

    def set_clean_available(self, available: bool) -> None:
        """Enable Cleaner actions only when the required MKVToolNix tools exist."""
        self.clean_available = bool(available)
        if not self.clean_available:
            self.clean_paths.clear()
        if hasattr(self, "clean_selected_button"):
            self.clean_selected_button.configure(
                state="normal" if self.clean_available else "disabled")
        if hasattr(self, "table"):
            for iid in self.table.get_children():
                values = list(self.table.item(iid, "values"))
                if len(values) >= 13:
                    path = Path(iid)
                    if self.live is None or path not in self.live.items:
                        path = path.resolve()
                    values[12] = ("—" if str(values[3]) == "Missing" else
                                  _clean_text(path in self.clean_paths,
                                              self.clean_available))
                    self.table.item(iid, values=values)
            self._refresh_clean_all_heading()

    def toggle_all_clean(self) -> None:
        if not self.clean_available:
            return
        rows = [iid for iid in self.table.get_children()
                if _can_clean(str(self.table.set(iid, "status")))]
        paths = []
        for iid in rows:
            path = Path(iid)
            if self.live is None or path not in self.live.items:
                path = path.resolve()
            paths.append(path)
        enable = not paths or not all(path in self.clean_paths for path in paths)
        for iid, path in zip(rows, paths):
            if enable:
                self.clean_paths.add(path)
            else:
                self.clean_paths.discard(path)
            values = list(self.table.item(iid, "values"))
            if len(values) >= 13:
                values[12] = _clean_text(enable)
                self.table.item(iid, values=values)
        self._refresh_clean_all_heading()

    def clean_selected(self) -> None:
        if not self.clean_available:
            messagebox.showinfo(
                "Cleaner unavailable",
                "MKVToolNix was not found. Encoding remains available; install or "
                "configure MKVToolNix from the Cleaner tab to enable cleaning.",
                parent=self)
            return
        selected = [path for path in self.clean_paths
                    if self.table.exists(str(path))
                    and _can_clean(str(self.table.set(str(path), "status")))]
        if not selected:
            messagebox.showinfo(
                "Clean Selected", "Check Clean for one or more files first.", parent=self)
            return
        if self.clean_request_callback is None:
            messagebox.showerror(
                "Cleaner unavailable", "The integrated Cleaner tab is unavailable.", parent=self)
            return
        self.clean_request_callback(selected)
        for path in selected:
            self.clean_paths.discard(path)
            iid = str(path)
            if self.table.exists(iid):
                values = list(self.table.item(iid, "values"))
                values[12] = _clean_text(False)
                self.table.item(iid, values=values)
        self._refresh_clean_all_heading()

    def toggle_all_force_encode(self) -> None:
        if self.live is not None:
            eligible = [
                item for item in self.live.snapshot()
                if _can_force(item.status) or item.path in self.queued_force
            ]
            force_all = not eligible or not all(
                item.force_encode or item.path in self.force_encode_paths
                for item in eligible)
            eligible_paths = {item.path for item in eligible}
            for item in eligible:
                self.live.set_force_encode(item.path, force_all)
                if force_all:
                    self.force_encode_paths.add(item.path)
                else:
                    self.force_encode_paths.discard(item.path)
            for iid in self.table.get_children():
                path = Path(iid)
                if path not in eligible_paths:
                    continue
                self._set_file_forced(
                    iid, path, force_all,
                    list(self.table.item(iid, "values")),
                    refresh_heading=False)
            self._refresh_force_all_heading()
            return
        eligible = []
        # Force All is a global list control. Detached Treeview rows are only
        # filtered from view; they must not retain a different hidden state.
        for iid in self._all_table_rows():
            values = list(self.table.item(iid, "values"))
            path = Path(iid)
            if self.live is None or path not in self.live.items:
                path = path.resolve()
            if len(values) < 13 or not (_can_force(str(values[3]))
                                         or path in self.queued_force):
                continue
            eligible.append((iid, path, values))
        force_all = not eligible or not all(
            path in self.force_encode_paths for _, path, _ in eligible)
        for iid, path, values in eligible:
            self._set_file_forced(iid, path, force_all, values,
                                  refresh_heading=False)
        self._refresh_force_all_heading()

    def toggle_all_file_selection(self) -> None:
        if self.live is not None:
            hidden = getattr(self, "filtered_rows", set())
            eligible = [item for item in self.live.snapshot()
                        if _can_select(item.status)]
            visible = [item for item in eligible
                       if str(item.path) not in hidden]
            select_all = not visible or not all(item.selected for item in visible)
            for item in eligible:
                if str(item.path) in hidden:
                    self.live.set_selected(item.path, False)
                    self.file_selection[item.path] = False
                    self.force_encode_paths.discard(item.path)
            for item in visible:
                self._set_file_selected(item.path, select_all,
                                        refresh_heading=False)
            self._refresh_encode_all_heading()
            self._refresh_force_all_heading()
            return
        eligible = []
        hidden = getattr(self, "filtered_rows", set())
        for iid in self._all_table_rows():
            values = list(self.table.item(iid, "values"))
            if len(values) < 13 or not _can_select(str(values[3])):
                continue
            eligible.append((iid, values))
        visible = [(iid, values) for iid, values in eligible if iid not in hidden]
        select_all = not visible or not all(
            str(values[10]) == _encode_text(True) for _, values in visible)
        for iid, values in eligible:
            self._set_file_selected(
                Path(iid), select_all if iid not in hidden else False, values,
                                    refresh_heading=False)
        self._refresh_encode_all_heading()

    def _set_file_selected(self, path: Path, selected: bool,
                           values: list | None = None,
                           refresh_heading: bool = True) -> bool:
        path = Path(path)
        if self.live is None or path not in self.live.items:
            path = path.resolve()
        if self.live is None or not self.live.set_selected(path, selected):
            return False
        # A direct row click or Encode All choice must win over an earlier
        # automatic deselection caused by filtering.  Otherwise the next
        # inspection/status event can silently restore the old selection.
        getattr(self, "filter_auto_excluded", set()).discard(path)
        self.file_selection[path] = selected
        if not selected:
            self.force_encode_paths.discard(path)
        item = self.live.items[path]
        values = values or list(self.table.item(str(path), "values"))
        values[1] = _force_text(item.force_encode)
        values[3] = item.status
        inspection_error = getattr(self, "inspection_errors", {}).get(path)
        if inspection_error:
            values[8] = f"Inspection failed: {inspection_error}"
        elif not selected:
            values[8] = "Excluded from processing"
        elif path in getattr(self, "inspection_results", {}):
            values[8] = "Media inspected — Ready"
        else:
            values[8] = item.detail
        values[10] = _encode_text(selected)
        self.table.item(str(path), values=values)
        if refresh_heading:
            self._refresh_encode_all_heading()
            self._refresh_force_all_heading()
        return True

    def _discard_retired_session_events(self) -> None:
        """Remove display work belonging to a directory/session now replaced."""
        for pending in (getattr(self, "events", None),
                        getattr(self, "urgent_events", None)):
            if pending is None:
                continue
            with pending.mutex:
                pending.queue.clear()

    def _reset_directory_session(self) -> None:
        """Reset all state owned by the previous input-directory session.

        Application preferences, filters, the activity log, inspection cache,
        and completed-output safety history intentionally survive.  Everything
        that describes rows or pending work in one directory is replaced as a
        unit so overlapping paths in a parent/child folder cannot inherit stale
        state.
        """
        # Invalidate in-flight inspection workers before clearing queues.  A
        # worker may be inside ffprobe and unable to observe cancellation until
        # later; its generation prevents that late result from entering the
        # replacement directory session.
        self.inspection_generation = getattr(
            self, "inspection_generation", 0) + 1
        getattr(self, "inspection_cancel", threading.Event()).set()
        OptimizerGUI._discard_retired_session_events(self)

        # Treeview.get_children() returns attached rows only.  Filtered rows are
        # detached, not deleted, and filter_order also retains their iids.  If
        # those old iids survive a folder change, the final filter pass for the
        # new folder tries to move a row that no longer belongs to its table
        # view.  That Tcl error used to abort inspection completion before the
        # pending Start gate was released.
        old_iids = set(self.table.get_children())
        old_iids.update(getattr(self, "filter_order", ()))
        old_iids.update(getattr(self, "filtered_rows", ()))
        for iid in old_iids:
            if self.table.exists(iid):
                self.table.delete(iid)

        for attribute in (
                "filter_order", "filtered_rows", "filter_auto_excluded",
                "missing_row_values", "file_selection", "force_encode_paths",
                "clean_paths", "queued_reanalysis", "queued_force",
                "sample_variants", "progressive_samples", "progressive_targets",
                "retained_failed_outputs", "encode_failure_details",
                "deleted_backups", "dropped_entries", "inspection_results",
                "inspection_errors", "inspection_enqueued", "inspection_batch",
                "reanalyze_preserve"):
            value = getattr(self, attribute, None)
            if value is not None and hasattr(value, "clear"):
                value.clear()

        pending_inspection = getattr(self, "inspection_queue", None)
        if pending_inspection is not None:
            with pending_inspection.mutex:
                pending_inspection.queue.clear()

        self.reanalyze_target = None
        self.drag_iid = None
        self.manual_order_active = False
        self.start_pending = False
        self.last_file_completed = False
        self.current_cancel_event = None
        self.inspection_active_path = None
        self.inspection_cycle_active = False
        self.inspection_total = 0
        self.inspection_done = 0
        self.inspection_failed = 0
        self.inspection_started_at = 0.0
        self.directory_discovered_count = 0
        self.directory_scan_baseline_count = 0
        self.inventory_folders_scanned = 0
        self.inventory_files_scanned = 0
        self.inventory_top_level_folders = 0
        self.global_refresh_pending = False
        self.missing_refresh_pending = False
        getattr(self, "inspection_cancel", threading.Event()).clear()
        getattr(self, "directory_discovery_cancel", threading.Event()).clear()

        if hasattr(self, "progress"):
            self.progress.reset()
        if hasattr(self, "current_var"):
            self.current_var.set("Ready. Choose files and press Start.")
        if hasattr(self, "files_count_var"):
            self.files_count_var.set(_file_count_text(0, 0, 0))
        if hasattr(self, "_refresh_encode_all_heading"):
            self._refresh_encode_all_heading()
            self._refresh_force_all_heading()
            self._refresh_clean_all_heading()
            self._refresh_missing_button()

    # Compatibility name for older tests/extensions; all new directory changes
    # use the full reset above.
    def _clear_inventory_view_for_new_root(self) -> None:
        OptimizerGUI._reset_directory_session(self)

    def _idle_scan_tick(self, schedule_next: bool = True) -> None:
        """Discover while idle; never probe, sample, or encode from this path."""
        if schedule_next:
            self.after(1000, self._idle_scan_tick)
        if (self.running
                or any(thread is not None and thread.is_alive()
                       for thread in (self.idle_discovery, self.worker, self.watcher))):
            return
        try:
            config = load_config(None)
            folder = self.input_var.get().strip() or config.input_directory.strip()
            if not folder:
                self.scan_status_var.set("● ON — choose an input folder")
                return
            root = Path(folder)
            if not root.is_absolute():
                root = application_directory() / root
            root = root.resolve()
            root_changed = getattr(self, "last_idle_scan_root", None) != root
            initial_scan = root_changed or self.live is None
            quiet_rescan = not initial_scan
            manual_scan = getattr(self, "manual_scan_requested", False)
            if not self.scan_enabled.is_set() and not initial_scan and not manual_scan:
                return
            if getattr(self, "inspection_cycle_active", False):
                return
            output = Path(config.output_directory) if config.output_directory.strip() else root
            if not output.is_absolute():
                output = default_config_path().parent / output
            output = output.resolve()
            excluded = output if output != root and output.is_relative_to(root) else None
            now = time.monotonic()
            inventory_scale = max(
                getattr(self, "directory_discovered_count", 0),
                getattr(self, "inventory_files_scanned", 0),
                getattr(self, "inventory_folders_scanned", 0))
            adaptive_interval = _adaptive_rescan_interval(
                config.rescan_interval_seconds,
                inventory_scale,
                getattr(self, "last_idle_scan_duration", 0.0))
            if (getattr(self, "last_idle_scan_root", None) == root
                    and now - getattr(self, "last_idle_scan_started", 0.0)
                    < adaptive_interval and not manual_scan):
                self.scan_next_deadline = self.last_idle_scan_started + adaptive_interval
                self._show_scan_status(self.scan_enabled.is_set())
                return
            self.manual_scan_requested = False
            self.scan_configured_interval = config.rescan_interval_seconds
            self.last_idle_scan_root = root
            self.last_idle_scan_started = now
            self.scan_next_deadline = 0.0
            self.directory_scan_baseline_count = (
                0 if root_changed else getattr(self, "directory_discovered_count", 0))
            if (self.live is None or self.live.root != root
                    or self.live.excluded_root != excluded
                    or self.live.allowed_paths is not None):
                previous = self.live
                if previous is None or previous.root != root:
                    OptimizerGUI._reset_directory_session(self)
                self.live = LiveQueue(root, config.file_ready_seconds, excluded,
                                      default_selected=config.select_new_files_for_encoding)
                self.live.add_manual(list(getattr(self, "dropped_entries", {}).items()))
                if previous is not None and previous.root == root:
                    self.live.completed.update(previous.completed)
                else:
                    # Preserve only cross-session output protection. The new
                    # directory still receives a completely fresh visible
                    # queue, while files created earlier in this application
                    # run cannot immediately re-enter it from a parent folder.
                    for source, output_path in getattr(
                            self, "completed_outputs", {}).items():
                        for protected in (source, output_path):
                            try:
                                if protected.is_relative_to(root):
                                    self.live.completed.add(protected)
                            except (OSError, ValueError):
                                continue
            live = self.live
            live.ready_seconds = config.file_ready_seconds
        except Exception as exc:
            if hasattr(self, "inventory_status_var"):
                self.inventory_status_var.set(f"Unavailable: {exc}")
            return
        self.directory_discovery_active = True
        self.idle_discovery_quiet = quiet_rescan
        if hasattr(self, "scan_now_button"):
            self.scan_now_button.configure(state="disabled")
        inventory_callback = getattr(self, "inventory_state_callback", None)
        if inventory_callback:
            inventory_callback(True)
        self.inspection_inventory_gate.clear()
        if hasattr(self, "cancel_inventory_button"):
            self.cancel_inventory_button.configure(state="normal")
        discovery_cancel = getattr(self, "directory_discovery_cancel", None)
        if discovery_cancel is None:
            discovery_cancel = self.directory_discovery_cancel = threading.Event()
        discovery_cancel.clear()
        if not quiet_rescan and hasattr(self, "files_count_var"):
            self.files_count_var.set("Files (scanning…)")
        self._show_scan_status(self.scan_enabled.is_set(), scanning=True)
        if not quiet_rescan and hasattr(self, "inventory_status_var"):
            self.inventory_status_var.set("Preparing directory inventory…")
            self.inventory_progress["value"] = 0
        if not quiet_rescan and getattr(self, "inspect_new_files", False):
            self.inspection_status_var.set(
                "Waiting for directory inventory to finish — inspection has not started")
            self.inspection_progress["value"] = 0
            self.cancel_inspection_button.configure(state="disabled")
        def discover_idle():
            scan_started = time.monotonic()
            try:
                if not root.is_dir():
                    raise ValueError(f"Input folder unavailable: {root}")
                inventory = {"folders": 0, "files": 0, "top_level": 0}
                def report_batch(items, count):
                    acknowledged = (threading.Event()
                                    if getattr(self, "discovery_batch_backpressure", False)
                                    else None)
                    self._emit(
                        "idle_discovery_batch", live, items, count, acknowledged)
                    if acknowledged is not None:
                        while not acknowledged.wait(0.1):
                            if discovery_cancel.is_set():
                                break
                def report_inventory(phase, folders, files, top_level):
                    inventory.update(
                        folders=folders, files=files, top_level=top_level)
                    if not quiet_rescan:
                        self._emit(
                            "idle_inventory_progress", live, phase, folders,
                            files, top_level)
                previous_statuses = {
                    item.path: item.status for item in live.snapshot()
                }
                live.refresh_incremental(
                    batch_size=25, batch_callback=report_batch,
                    discovery_callback=report_inventory,
                    cancelled=discovery_cancel.is_set,
                    existence_paths=getattr(self, "completed_outputs", {}),
                    folder_delay_seconds=(
                        _background_folder_delay(
                            inventory_scale, False)
                        if quiet_rescan else 0.0))
                if discovery_cancel.is_set():
                    self._emit("idle_discovery_cancelled", live)
                    return
                live.finalize_inventory()
                for item in _newly_missing_items(
                        previous_statuses, live.snapshot()):
                    self._emit(
                        "row", item.path, item.status, "—", "—",
                        _size_text(item.size), "—", item.detail)
                self._emit(
                    "idle_discovered", live, live.last_discovered_count,
                    inventory["folders"], inventory["files"],
                    inventory["top_level"], quiet_rescan,
                    time.monotonic() - scan_started)
            except Exception as exc:
                self._emit("idle_scan_error", str(exc))
        self.idle_discovery = threading.Thread(target=discover_idle, daemon=True)
        self.idle_discovery.start()

    def _browse(self) -> None:
        selected = filedialog.askdirectory(title="Choose the video input folder")
        if selected:
            self.input_var.set(selected)

    def go_home(self) -> None:
        """Return the stopped Encoder to its configured default directory."""
        if self.running or getattr(self, "start_pending", False):
            return
        try:
            config = load_config(default_config_path())
            if not config.input_directory.strip():
                raise ValueError("No default input folder is configured.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot open Home folder", str(exc), parent=self)
            return
        self.input_var.set(config.input_directory)
        self.manual_scan_requested = True
        self.last_idle_scan_started = 0.0
        # Retire work for the previous folder; the normal idle scanner performs
        # the authoritative session reset once those workers have acknowledged
        # cancellation.
        getattr(self, "directory_discovery_cancel", threading.Event()).set()
        getattr(self, "inspection_cancel", threading.Event()).set()
        self.inventory_status_var.set("Returning to default input folder…")
        self.after(100, self._finish_home_navigation)

    def _finish_home_navigation(self) -> None:
        """Start the Home inventory after prior-folder workers have retired."""
        if self.running or getattr(self, "start_pending", False):
            return
        busy = (getattr(self, "directory_discovery_active", False)
                or getattr(self, "inspection_cycle_active", False)
                or any(thread is not None and thread.is_alive()
                       for thread in (self.idle_discovery,
                                      self.inspection_thread)))
        if busy:
            self.after(100, self._finish_home_navigation)
            return
        self._idle_scan_tick(schedule_next=False)

    def add_dropped_paths(self, dropped: list[str | Path]) -> int:
        """Add Explorer-dropped video files or folders to the visible queue."""
        entries = expand_dropped_paths(dropped, VIDEO_EXTENSIONS)
        if not entries:
            return 0
        try:
            config = load_config(None)
            root = Path(self.input_var.get() or config.input_directory)
            if not root.is_absolute():
                root = application_directory() / root
            root = root.resolve()
            if self.live is None:
                output = (Path(config.output_directory)
                          if config.output_directory.strip() else root)
                if not output.is_absolute():
                    output = default_config_path().parent / output
                output = output.resolve()
                excluded = output if output != root and output.is_relative_to(root) else None
                self.live = LiveQueue(
                    root, config.file_ready_seconds, excluded,
                    default_selected=config.select_new_files_for_encoding)
            normalized: list[tuple[Path, Path]] = []
            for path, dropped_root in entries:
                source_root = root if path.is_relative_to(root) else dropped_root
                self.dropped_entries[path] = source_root
                normalized.append((path, source_root))
            added = self.live.add_manual(normalized)
        except (OSError, ValueError) as exc:
            self._log(f"Could not add dropped files: {exc}")
            return 0
        for item in added:
            self._emit("row", item.path, item.status, "—", "—",
                       _size_text(item.size), "—", "Dropped — awaiting Start")
        if self.inspect_new_files:
            self.queue_inspection([item.path for item in added])
        self._log(f"Added {len(added)} dropped video file(s) to the queue.")
        return len(added)

    def _settings(self) -> None:
        try:
            SettingsDialog(self, load_config(default_config_path()))
        except Exception as exc:
            messagebox.showerror("Cannot load settings", str(exc))

    def _open_user_guide(self) -> None:
        guide = application_directory() / "USER_GUIDE.html"
        if not guide.is_file():
            messagebox.showerror(
                "User Guide not found",
                f"USER_GUIDE.html was not found beside the application:\n\n{guide}",
                parent=self)
            return
        try:
            if os.name == "nt":
                os.startfile(str(guide))  # type: ignore[attr-defined]
            else:
                webbrowser.open(guide.as_uri())
        except OSError as exc:
            messagebox.showerror("Cannot open User Guide", str(exc), parent=self)

    def _table_motion(self, event) -> None:
        row = self.table.identify_row(event.y)
        column = self.table.identify_column(event.x)
        clickable = False
        if row and column == _display_column_number("action"):
            values = self.table.item(row, "values")
            status = str(values[3]) if len(values) > 3 else ""
            clickable = (_can_reanalyze(status, self.running)
                         or (status == "Completed" and _encoder_play_path(
                             Path(row), status, self.completed_outputs) is not None))
        elif row and column == _display_column_number("backup"):
            values = self.table.item(row, "values")
            backup_state = str(values[15]) if len(values) > 15 else ""
            status = str(values[3]) if len(values) > 3 else ""
            clickable = (backup_state == "[ Present ]"
                         or status in {"Completed", "Skipped", "Failed"})
        elif row and column == _display_column_number("force"):
            values = self.table.item(row, "values")
            row_path = Path(row)
            clickable = (len(values) > 3
                         and (_can_force(str(values[3]))
                              or row_path in self.queued_force))
        elif row and column == _display_column_number("selected"):
            values = self.table.item(row, "values")
            clickable = len(values) >= 13 and _can_select(str(values[3]))
        draggable = False
        if row and column == "#1":
            values = self.table.item(row, "values")
            draggable = len(values) > 3 and _can_reorder(str(values[3]))
        self.table.configure(cursor="fleur" if draggable else "hand2" if clickable else "")

    def _table_press(self, event) -> None:
        self.drag_iid = None
        if self.table.identify_column(event.x) != "#1":
            return
        iid = self.table.identify_row(event.y)
        if iid:
            values = self.table.item(iid, "values")
            if len(values) > 3 and _can_reorder(str(values[3])):
                self.drag_iid = iid

    def _table_drag(self, event) -> None:
        if not self.drag_iid:
            return
        target = self.table.identify_row(event.y)
        if not target or target == self.drag_iid:
            return
        target_values = self.table.item(target, "values")
        if len(target_values) <= 3 or not _can_reorder(str(target_values[3])):
            return
        self.table.move(self.drag_iid, "", self.table.index(target))

    def _sync_queue_order(self, manual: bool = False) -> None:
        if manual:
            self.manual_order_active = True
        if self.live is not None:
            visible = list(self.table.get_children())
            if self.filter_order:
                # Treeview.get_children() contains only attached (visible)
                # rows. Merge their newly dragged order back into the visible
                # slots of the complete filtered order, preserving hidden rows
                # without letting the old sort order undo the drag.
                visible_set = set(visible)
                visible_iter = iter(visible)
                ordered = [
                    next(visible_iter) if iid in visible_set else iid
                    for iid in self.filter_order]
                known = set(ordered)
                ordered.extend(iid for iid in visible if iid not in known)
                self.filter_order = ordered
            else:
                self.filter_order = visible
            self.live.reorder([Path(iid) for iid in self.filter_order])

    def _sync_visible_selection_for_start(self) -> int:
        """Freeze selection to the visible subset before claiming work."""
        if self.live is None:
            return 0
        hidden = getattr(self, "filtered_rows", set())
        selected_count = 0
        for item in self.live.snapshot():
            if not _can_select(item.status):
                continue
            iid = str(item.path)
            checked = item.selected and iid not in hidden
            self.live.set_selected(item.path, checked)
            self.file_selection[item.path] = checked
            if not checked:
                self.force_encode_paths.discard(item.path)
            else:
                selected_count += 1
        return selected_count

    def _table_click(self, event) -> None:
        column = self.table.identify_column(event.x)
        if self.drag_iid is not None:
            self._sync_queue_order(manual=True)
            self.drag_iid = None
            return
        iid = self.table.identify_row(event.y)
        if column == _display_column_number("selected") and iid:
            values = list(self.table.item(iid, "values"))
            if len(values) >= 13 and _can_select(str(values[3])):
                selected = str(values[10]) != _encode_text(True)
                self._set_file_selected(Path(iid), selected, values)
            return
        if column == _display_column_number("force") and iid:
            values = list(self.table.item(iid, "values"))
            path = Path(iid)
            if len(values) > 3 and (_can_force(str(values[3]))
                                    or path in self.queued_force):
                enabled = path not in self.force_encode_paths
                self._set_file_forced(iid, path, enabled, values)
            return
        if column == _display_column_number("backup") and iid:
            self._backup_cell_clicked(iid)
            return
        if column != _display_column_number("action"):
            return
        if not iid:
            return
        values = self.table.item(iid, "values")
        if len(values) <= 3:
            return
        status = str(values[3])
        if status == "Completed":
            self._play_video(_encoder_play_path(
                Path(iid), status, self.completed_outputs))
        elif _can_reanalyze(status, self.running):
            self.reanalyze(Path(iid))

    def _set_file_clean(self, iid: str, enabled: bool) -> bool:
        path = Path(iid).resolve()
        values = list(self.table.item(iid, "values"))
        if (not self.clean_available or len(values) <= 3
                or not _can_clean(str(values[3]))):
            return False
        if enabled:
            self.clean_paths.add(path)
        else:
            self.clean_paths.discard(path)
        values[12] = _clean_text(enabled)
        self.table.item(iid, values=values)
        self._refresh_clean_all_heading()
        return True

    def _show_selected_file_menu(self, event=None):
        selected = self.table.selection()
        if selected:
            bounds = self.table.bbox(selected[0])
            x = self.table.winfo_rootx() + (bounds[0] if bounds else 0) + 20
            y = self.table.winfo_rooty() + (bounds[1] if bounds else 0) + 20
            self._open_file_menu(selected[0], x, y)
        return "break"

    def _show_file_menu(self, event):
        iid = self.table.identify_row(event.y)
        if not iid:
            return
        if iid not in self.table.selection():
            self.table.selection_set(iid)
        self.table.focus(iid)
        self._open_file_menu(iid, event.x_root, event.y_root)

    def _open_file_menu(self, iid: str, x: int, y: int) -> None:
        values = list(self.table.item(iid, "values"))
        if len(values) < 13:
            return
        path = Path(iid).resolve()
        status = str(values[3])
        selected_rows = list(self.table.selection()) or [iid]
        selected_data = [
            (row, Path(row).resolve(), list(self.table.item(row, "values")))
            for row in selected_rows if self.table.exists(row)
        ]
        menu = tk.Menu(self, tearoff=False)
        play_path = _encoder_play_path(path, status, self.completed_outputs)
        menu.add_command(
            label="Play Video",
            command=lambda target=play_path: self._play_video(target),
            state="normal" if play_path is not None else "disabled")
        menu.add_separator()
        reanalyzable = [(row, item_path) for row, item_path, item_values in selected_data
                        if _can_reanalyze(str(item_values[3]), self.running)]
        menu.add_command(
            label=("Reanalyze" if len(reanalyzable) <= 1
                   else f"Reanalyze {len(reanalyzable)} Selected Files"),
            command=lambda items=reanalyzable:
                [self.reanalyze(item_path) for _row, item_path in items],
            state="normal" if reanalyzable else "disabled")
        force_eligible = [(row, item_path, item_values)
                          for row, item_path, item_values in selected_data
                          if (_can_force(str(item_values[3]))
                              or item_path in self.queued_force)]
        force_enable = not force_eligible or not all(
            item_path in self.force_encode_paths
            for _row, item_path, _values in force_eligible)
        menu.add_command(
            label=("Force Encode Selected" if force_enable
                   else "Remove Force Encode From Selected"),
            command=lambda items=force_eligible, enabled=force_enable:
                self._set_selected_forced(items, enabled),
            state="normal" if force_eligible else "disabled")
        select_eligible = [(row, item_path, item_values)
                           for row, item_path, item_values in selected_data
                           if _can_select(str(item_values[3]))]
        select_enable = not select_eligible or not all(
            str(item_values[10]) == _encode_text(True)
            for _row, _path, item_values in select_eligible)
        menu.add_command(
            label="Encode Selected" if select_enable else "Do Not Encode Selected",
            command=lambda items=select_eligible, enabled=select_enable:
                self._set_selected_encode(items, enabled),
            state="normal" if select_eligible else "disabled")
        clean_eligible = [item_path for _row, item_path, item_values in selected_data
                          if _can_clean(str(item_values[3]))]
        menu.add_command(
            label=("Send to Cleaner" if len(clean_eligible) <= 1
                   else f"Send {len(clean_eligible)} Selected Files to Cleaner"),
            command=lambda paths=clean_eligible: self._send_to_cleaner(paths),
            state="normal" if self.clean_available and clean_eligible
            else "disabled")
        menu.add_command(
            label="Send All Files in This Directory to Cleaner...",
            command=lambda folder=self._rename_target(path).parent:
                self._send_directory_to_cleaner(folder),
            state="normal" if self.clean_available else "disabled")
        menu.add_command(
            label="Send All Eligible Files to Cleaner...",
            command=self._send_all_to_cleaner,
            state="normal" if self.clean_available else "disabled")
        menu.add_command(
            label=("Inspect File" if len(selected_data) <= 1
                   else f"Inspect {len(selected_data)} Selected Files"),
            command=lambda paths=[item_path for _row, item_path, _values in selected_data]:
                self.queue_inspection(paths))
        menu.add_separator()
        menu.add_command(label="Select All Rows", command=self._select_all_rows)
        menu.add_command(label="Clear Row Selection", command=self._clear_row_selection)
        menu.add_separator()
        movable_rows = [row for row, _path, item_values in selected_data
                        if _can_reorder(str(item_values[3]))]
        movable = bool(movable_rows) and len(movable_rows) == len(selected_data)
        for label, destination in (
                ("Move to Top", "top"), ("Move Up", "up"),
                ("Move Down", "down"), ("Move to Bottom", "bottom")):
            menu.add_command(
                label=label,
                command=lambda where=destination, rows=movable_rows:
                    self._move_rows(rows, where),
                state="normal" if movable else "disabled")
        menu.add_separator()
        rename_paths = [self._rename_target(Path(row).resolve())
                        for row in selected_rows]
        menu.add_command(
            label=("Send File to Rename Tab" if len(rename_paths) == 1
                   else f"Send {len(rename_paths)} Files to Rename Tab"),
            command=lambda paths=rename_paths: self.rename_request_callback(paths),
            state="normal" if self.rename_request_callback else "disabled")
        menu.add_command(
            label="Add This Directory to Rename Tab...",
            command=lambda folder=self._rename_target(path).parent:
                self.rename_directory_callback(folder),
            state="normal" if self.rename_directory_callback else "disabled")
        menu.add_command(label="Open File Location",
                         command=lambda: self._open_file_location(path))
        menu.add_command(label="Copy Filename",
                         command=lambda: self._copy_text(path.name))
        menu.add_command(label="Copy Full Path",
                         command=lambda: self._copy_text(str(path)))
        menu.add_command(
            label="Remove From List",
            command=lambda rows=selected_rows: self._remove_rows_from_list(rows),
            state="normal" if all(
                str(self.table.set(row, "status")) not in {"Sampling", "Encoding", "Paused"}
                for row in selected_rows) else "disabled")
        if status == "Completed":
            config = getattr(self, "run_config", None) or load_config(None)
            backup = _backup_path(path, config.backup_suffix)
            selected_backups = []
            seen_backups: set[Path] = set()
            for _row, selected_path, selected_values in selected_data:
                if str(selected_values[3]) != "Completed":
                    continue
                selected_backup = _backup_path(
                    selected_path, config.backup_suffix)
                if (selected_backup not in seen_backups
                        and selected_backup.is_file()):
                    selected_backups.append((selected_backup, selected_path))
                    seen_backups.add(selected_backup)
            menu.add_separator()
            menu.add_command(
                label="Revert to Backup...",
                command=lambda: self._revert_to_backup(path),
                state="normal" if backup.is_file() else "disabled")
            menu.add_command(
                label=("Delete Backup File..." if len(selected_backups) <= 1
                       else f"Delete {len(selected_backups)} Backup Files..."),
                command=lambda entries=selected_backups:
                    self._delete_backups(entries),
                state="normal" if selected_backups else "disabled")
        menu.add_separator()
        menu.add_command(
            label="File Details...",
            command=lambda item_path=path, item_values=values:
                self._show_file_details(item_path, item_values))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _move_row(self, iid: str, destination: str) -> None:
        self._move_rows([iid], destination)

    def _move_rows(self, rows: list[str], destination: str) -> None:
        current = list(self.table.get_children())
        order = _moved_row_order(current, rows, destination)
        for index, row in enumerate(order):
            self.table.move(row, "", index)
        self._sync_queue_order(manual=True)

    def _set_selected_forced(self, items, enabled: bool) -> None:
        for row, path, values in items:
            self._set_file_forced(row, path, enabled, values, refresh_heading=False)
        self._refresh_force_all_heading()

    def _set_selected_encode(self, items, enabled: bool) -> None:
        for _row, path, values in items:
            self._set_file_selected(path, enabled, values, refresh_heading=False)
        self._refresh_encode_all_heading()
        self._refresh_force_all_heading()

    def _send_to_cleaner(self, paths: list[Path]) -> None:
        if self.clean_request_callback and paths:
            self.clean_request_callback(paths)
            for path in paths:
                iid = str(path.resolve())
                if self.table.exists(iid):
                    self._set_file_clean(iid, False)

    def _send_all_to_cleaner(self) -> None:
        paths = [Path(iid).resolve() for iid in self._all_table_rows()
                 if _can_clean(str(self.table.set(iid, "status")))]
        if not paths:
            messagebox.showinfo(
                "Send all to Cleaner", "There are no eligible files to send.",
                parent=self)
            return
        if messagebox.askyesno(
                "Send all to Cleaner",
                f"Send all {len(paths)} eligible files to the Cleaner tab?",
                parent=self):
            self._send_to_cleaner(paths)

    def _send_directory_to_cleaner(self, folder: Path) -> None:
        """Send every eligible inventory row represented by one exact folder."""
        folder = Path(folder)
        paths = []
        for iid in self._all_table_rows():
            source = Path(iid).resolve()
            if (self._rename_target(source).parent == folder
                    and _can_clean(str(self.table.set(iid, "status")))):
                paths.append(source)
        if not paths:
            messagebox.showinfo(
                "Send directory to Cleaner",
                "There are no eligible files from this directory to send.",
                parent=self)
            return
        if messagebox.askyesno(
                "Send directory to Cleaner",
                f"Send all {len(paths)} eligible file"
                f"{'s' if len(paths) != 1 else ''} from:\n\n{folder}\n\n"
                "to the Cleaner tab?",
                parent=self):
            self._send_to_cleaner(paths)

    def _select_all_rows(self, event=None):
        rows = self.table.get_children()
        if rows:
            self.table.selection_set(rows)
        return "break" if event is not None else None

    def _clear_row_selection(self) -> None:
        self.table.selection_remove(self.table.selection())

    def _remove_rows_from_list(self, rows: list[str]) -> None:
        for row in list(rows):
            if self.table.exists(row):
                self._remove_row_from_list(Path(row))

    def remove_missing_files(self) -> None:
        """Remove all Missing rows from this session without touching disk."""
        rows = [iid for iid in self.table.get_children()
                if str(self.table.set(iid, "status")) == "Missing"]
        self._remove_rows_from_list(rows)
        self._refresh_missing_button()

    def _refresh_missing_button(self) -> None:
        if not hasattr(self, "remove_missing_button"):
            return
        has_missing = any(
            str(self.table.set(iid, "status")) == "Missing"
            for iid in self.table.get_children())
        self.remove_missing_button.configure(
            state="normal" if has_missing else "disabled")

    def _open_file_location(self, path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path.parent))
            else:
                webbrowser.open(path.parent.as_uri())
        except OSError as exc:
            messagebox.showerror("Cannot open folder", str(exc), parent=self)

    def _play_video(self, path: Path | None) -> None:
        if path is None or not path.is_file():
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                webbrowser.open(path.as_uri())
        except OSError as exc:
            messagebox.showerror("Cannot play video", str(exc), parent=self)

    def _rename_target(self, source: Path) -> Path:
        """Return the actual file represented by an Encoder row."""
        return self.completed_outputs.get(source.resolve(), source.resolve())

    def _marked_filename(self, path: Path) -> str:
        """Format a filename using the current optional attention markers."""
        return _filename_text(
            path,
            getattr(self, "mark_filenames_with_spaces", True),
            getattr(self, "mark_filenames_missing_year_or_episode", True),
        )

    def _refresh_filename_markers(self) -> None:
        """Refresh filename cells immediately after Appearance settings save."""
        for iid in self._all_table_rows():
            if not self.table.exists(iid):
                continue
            source = Path(iid)
            displayed_path = self.completed_outputs.get(source, source)
            values = list(self.table.item(iid, "values"))
            if len(values) > 2:
                values[2] = OptimizerGUI._marked_filename(self, displayed_path)
                self.table.item(iid, values=values)

    @staticmethod
    def _replace_path_in_set(values: set[Path], old: Path, new: Path) -> None:
        if old in values:
            values.discard(old)
            values.add(new)

    def paths_renamed(self, mapping: dict[Path, Path]) -> None:
        """Migrate every Encoder reference after Rename changes disk paths."""
        # These paths already came from the Encoder/Rename queues. Keep this
        # in-memory operation off the NAS: this method also updates lists with
        # thousands of entries and used to resolve every one on Tk's thread.
        resolved = {Path(old): Path(new) for old, new in mapping.items()}
        displayed_paths = {
            path for path in {*resolved, *self.completed_outputs}
            if self.table.exists(str(path))
        }
        source_mapping = _logical_rename_mapping(
            resolved, self.completed_outputs, displayed_paths)
        if self.live is not None:
            self.live.rename_paths(source_mapping)
        if self.reanalyze_target in source_mapping:
            self.reanalyze_target = source_mapping[self.reanalyze_target]
        self.reanalyze_preserve = {
            source_mapping.get(path, path)
            for path in getattr(self, "reanalyze_preserve", set())}
        self.force_encode_paths = {
            source_mapping.get(path, path) for path in self.force_encode_paths}
        self.clean_paths = {
            source_mapping.get(path, path) for path in self.clean_paths}
        for attribute in (
                "file_selection", "sample_variants", "progressive_samples",
                "progressive_targets",
                "queued_reanalysis", "queued_force", "missing_row_values",
                "retained_failed_outputs", "encode_failure_details"):
            values = getattr(self, attribute, {})
            setattr(self, attribute, {
                source_mapping.get(path, path): value
                for path, value in values.items()
            })
        self.dropped_entries = {
            source_mapping.get(path, path): source_mapping.get(root, root)
            for path, root in self.dropped_entries.items()}
        self.deleted_backups = {
            source_mapping.get(path, path) for path in self.deleted_backups}
        self.completed_outputs = {
            source_mapping.get(source, source): resolved.get(output, output)
            for source, output in self.completed_outputs.items()
        }
        self.inspection_results = {
            source_mapping.get(path, path): replace(
                result, path=source_mapping.get(result.path, result.path))
            for path, result in self.inspection_results.items()}
        self.inspection_errors = {
            source_mapping.get(path, path): error
            for path, error in self.inspection_errors.items()
        }
        with self.inspection_lock:
            pending_inspections = []
            while True:
                try:
                    pending_inspections.append(
                        self.inspection_queue.get_nowait())
                except queue.Empty:
                    break
            self.inspection_enqueued = {
                source_mapping.get(path, path)
                for path in self.inspection_enqueued
            }
            for path in pending_inspections:
                self.inspection_queue.put(source_mapping.get(path, path))
            self.inspection_batch = [
                source_mapping.get(path, path)
                for path in getattr(self, "inspection_batch", [])]

        def migrate_event_value(value):
            if isinstance(value, Path):
                return source_mapping.get(value, value)
            if isinstance(value, MediaInspection):
                destination = source_mapping.get(value.path, value.path)
                return replace(value, path=destination)
            if isinstance(value, tuple):
                return tuple(migrate_event_value(item) for item in value)
            if isinstance(value, list):
                return [migrate_event_value(item) for item in value]
            return value

        # A worker may already have posted a row or inspection result under the
        # old path. Rewrite queued events before Tk consumes them, otherwise a
        # late event could recreate the former filename after the rename.
        for events in (getattr(self, "events", None),
                       getattr(self, "urgent_events", None)):
            if events is None:
                continue
            with events.mutex:
                migrated_events = [
                    migrate_event_value(event) for event in events.queue]
                events.queue.clear()
                events.queue.extend(migrated_events)
        drag_iid = getattr(self, "drag_iid", None)
        if drag_iid is not None:
            self.drag_iid = str(source_mapping.get(Path(drag_iid), Path(drag_iid)))
        for old, new in source_mapping.items():
            self.inspection_cache.rename(old, new)
        self.filter_order = [str(source_mapping.get(Path(iid), Path(iid)))
                             for iid in self.filter_order]
        self.filtered_rows = {
            str(source_mapping.get(Path(iid), Path(iid)))
            for iid in self.filtered_rows}
        row_records: list[tuple[int, Path, list, tuple, bool]] = []
        for old, new in source_mapping.items():
            iid = str(old)
            row_records.append((
                self.table.index(iid), new, list(self.table.item(iid, "values")),
                self.table.item(iid, "tags"), iid in self.table.selection()))
        for old in source_mapping:
            self.table.delete(str(old))
        for index, new, values, tags, selected in sorted(row_records):
            values[0] = "☰"
            values[2], values[11] = OptimizerGUI._marked_filename(
                self, new), str(new.parent)
            iid = str(new)
            self.table.insert("", index, iid=iid, values=values, tags=tags)
            if selected:
                self.table.selection_add(iid)
        for source, output in self.completed_outputs.items():
            iid = str(source)
            if self.table.exists(iid) and output in resolved.values():
                values = list(self.table.item(iid, "values"))
                values[2], values[11] = OptimizerGUI._marked_filename(
                    self, output), str(output.parent)
                self.table.item(iid, values=values)
        # Rebuilding a Treeview row attaches it even when its prior row was
        # filtered out.  Regenerate the view from the migrated inventory so a
        # rename cannot make hidden files appear or leave selected files
        # outside the active filter.
        apply_view = getattr(self, "apply_media_view", None)
        if callable(apply_view):
            apply_view()
        self._refresh_force_all_heading()
        self._refresh_encode_all_heading()
        self._refresh_clean_all_heading()
        self._refresh_missing_button()

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _file_details_text(self, path: Path, values: list) -> str:
        """Build copy-friendly details for one Encoder inventory row."""
        path = path.resolve()
        fields = (
            ("File name", values[2]), ("Path", values[11]),
            ("Full path", path), ("Status", values[3]),
            ("Progress", values[4]), ("Current file size", values[6]),
            ("Projected output", values[7]),
            ("Projected savings", values[5]),
            ("Decision / result", values[8]), ("Action", values[9]),
            ("Backup", values[15] if len(values) > 15 else "—"),
            ("Force Encode", "Yes" if path in self.force_encode_paths else "No"),
            ("Selected for encoding",
             "Yes" if str(values[10]) == _encode_text(True) else "No"),
        )
        lines = [f"{label}: {value}" for label, value in fields]
        output = self.completed_outputs.get(path)
        if output is not None:
            lines.extend(("", "Completed output:", f"  {output}"))
        failure = self.encode_failure_details.get(path)
        retained = self.retained_failed_outputs.get(path)
        if failure or retained is not None:
            lines.extend(("", "Encoding failure details:"))
            if failure:
                lines.append(f"  Error: {failure}")
            if retained is not None:
                lines.append(f"  Retained encoded output: {retained}")
                try:
                    stat = retained.stat()
                    lines.extend((
                        "  Retained file exists: Yes",
                        f"  Retained file size: {_size_text(stat.st_size)}",
                        f"  Retained file modified: {time.ctime(stat.st_mtime)}",
                    ))
                except OSError:
                    lines.append("  Retained file exists: No")
        inspection = self.inspection_results.get(path)
        lines.extend(("", "Media inspection:"))
        if inspection is None:
            lines.append("  Not available")
        else:
            bitrate = (f"{inspection.bit_rate / 1_000_000:.2f} Mbps"
                       if inspection.bit_rate else "Unknown")
            duration = (_duration_text(inspection.duration)
                        if inspection.duration is not None else "Unknown")
            lines.extend((
                f"  Codec: {inspection.codec_label} ({inspection.codec})",
                f"  Resolution: {inspection.resolution}",
                f"  Resolution group: {inspection.resolution_group}",
                f"  Bitrate: {bitrate}", f"  Duration: {duration}",
                f"  Rating: {inspection.rating}",
            ))
        error = self.inspection_errors.get(path)
        if error:
            lines.extend(("", "Inspection error:", f"  {error}"))
        return "\n".join(str(line) for line in lines)

    def _show_file_details(self, path: Path, values: list) -> None:
        details = self._file_details_text(path, values)
        colors = getattr(self, "theme_colors", {
            "field": "#ffffff", "fg": "#202124", "select": "#cde4ff",
            "border": "#b8b8b8",
        })
        dialog = tk.Toplevel(self)
        dialog.title(f"File Details — {path.name}")
        dialog.geometry("720x560")
        dialog.minsize(560, 400)
        dialog.transient(self.winfo_toplevel())
        body = ttk.Frame(dialog, padding=10)
        body.pack(fill="both", expand=True)
        text = tk.Text(body, wrap="word", relief="solid", borderwidth=1,
                       font=("Consolas", 10), padx=10, pady=10,
                       background=colors["field"], foreground=colors["fg"],
                       insertbackground=colors["fg"],
                       selectbackground=colors["select"],
                       highlightbackground=colors["border"])
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", details)
        text.configure(state="disabled")
        buttons = ttk.Frame(dialog, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Copy All",
                   command=lambda: self._copy_text(details)).pack(side="left")
        ttk.Button(buttons, text="Close", command=dialog.destroy).pack(side="right")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.focus_set()

    def _remove_row_from_list(self, path: Path) -> None:
        resolved = path.resolve()
        if self.live is not None:
            self.live.dismiss(resolved)
        iid = str(resolved)
        if self.table.exists(iid):
            self.table.delete(iid)
        self.force_encode_paths.discard(resolved)
        self.clean_paths.discard(resolved)
        self.file_selection.pop(resolved, None)
        self.missing_row_values.pop(resolved, None)
        self.inspection_results.pop(resolved, None)
        self.inspection_errors.pop(resolved, None)
        self.retained_failed_outputs.pop(resolved, None)
        self.encode_failure_details.pop(resolved, None)
        self.filtered_rows.discard(iid)
        self.filter_order = [row for row in self.filter_order if row != iid]
        self._refresh_force_all_heading()
        self._refresh_encode_all_heading()
        self._refresh_clean_all_heading()
        self._refresh_missing_button()

    def _backup_status(self, source: Path, status: str, check_disk: bool = True) -> str:
        source = Path(source)
        live = getattr(self, "live", None)
        if live is None or source not in live.items:
            source = source.resolve()
        config = getattr(self, "run_config", None) or load_config(None)
        if status == "Reverted":
            return "Restored"
        if status not in {"Completed", "Skipped", "Failed"}:
            return "—"
        backup = _backup_path(source, config.backup_suffix)
        if check_disk and backup.is_file():
            return "[ Present ]"
        if source in self.deleted_backups:
            return "Deleted"
        if not config.backup_originals:
            return "Disabled"
        if status == "Skipped":
            return "Not created"
        return "Not found" if status in {"Completed", "Failed"} else "—"

    def _refresh_backup_cell(self, source: Path, check_disk: bool = True) -> None:
        source = source.resolve()
        iid = str(source)
        if not self.table.exists(iid):
            return
        status = str(self.table.set(iid, "status"))
        self.table.set(iid, "backup", self._backup_status(source, status, check_disk))

    def _backup_cell_clicked(self, iid: str) -> None:
        source = Path(iid).resolve()
        config = getattr(self, "run_config", None) or load_config(None)
        backup = _backup_path(source, config.backup_suffix)
        if backup.is_file():
            self._delete_backup(backup, source)
        else:
            self._refresh_backup_cell(source, check_disk=True)

    def _delete_backup(self, backup: Path, source: Path | None = None) -> None:
        if not backup.is_file():
            if source is not None:
                self._refresh_backup_cell(source, check_disk=True)
            messagebox.showinfo(
                "Backup not found", f"The backup file no longer exists:\n\n{backup}",
                parent=self)
            return
        if not messagebox.askyesno(
                "Delete backup file",
                "Permanently delete this backup file?\n\n"
                f"{backup}\n\nThis cannot be undone.", parent=self):
            return
        try:
            backup.unlink()
        except OSError as exc:
            messagebox.showerror(
                "Could not delete backup", f"{backup}\n\n{exc}", parent=self)
            return
        if source is not None:
            resolved = source.resolve()
            self.deleted_backups.add(resolved)
            self._refresh_backup_cell(resolved, check_disk=False)
        self._log(f"Deleted backup: {backup}")

    def _delete_backups(self, entries: list[tuple[Path, Path]]) -> None:
        """Delete exact selected backup paths after one batch confirmation."""
        existing: list[tuple[Path, Path, int]] = []
        for backup, source in entries:
            try:
                existing.append((Path(backup), Path(source), backup.stat().st_size))
            except OSError:
                continue
        if not existing:
            messagebox.showinfo(
                "No backup files found",
                "None of the selected completed files currently has a backup.",
                parent=self)
            return
        total_size = sum(size for _backup, _source, size in existing)
        names = "\n".join(f"• {backup.name}" for backup, _source, _size in existing[:12])
        if len(existing) > 12:
            names += f"\n• …and {len(existing) - 12} more"
        if not messagebox.askyesno(
                "Delete selected backup files",
                f"Permanently delete {len(existing)} selected backup "
                f"file{'s' if len(existing) != 1 else ''} "
                f"({_size_text(total_size)})?\n\n{names}\n\n"
                "Encoded video files will not be deleted. This cannot be undone.",
                parent=self):
            return
        failures: list[tuple[Path, str]] = []
        for backup, source, _size in existing:
            try:
                backup.unlink()
            except OSError as exc:
                failures.append((backup, str(exc)))
                self._refresh_backup_cell(source, check_disk=True)
                continue
            self.deleted_backups.add(source)
            self._refresh_backup_cell(source, check_disk=False)
            self._log(f"Deleted backup: {backup}")
        if failures:
            details = "\n".join(
                f"• {backup.name}: {error}" for backup, error in failures[:8])
            if len(failures) > 8:
                details += f"\n• …and {len(failures) - 8} more"
            messagebox.showwarning(
                "Some backups could not be deleted",
                f"Deleted {len(existing) - len(failures)} of {len(existing)} "
                f"backup files.\n\n{details}", parent=self)

    def _revert_to_backup(self, source: Path) -> None:
        """Restore one completed original and discard its encoded output."""
        source = source.resolve()
        config = getattr(self, "run_config", None) or load_config(None)
        backup = _backup_path(source, config.backup_suffix)
        output = self.completed_outputs.get(source, source)
        if not backup.is_file():
            self._refresh_backup_cell(source, check_disk=True)
            messagebox.showinfo(
                "Backup not found",
                f"The backup file no longer exists:\n\n{backup}", parent=self)
            return
        if not messagebox.askyesno(
                "Revert to backup",
                "Restore the original file from its backup?\n\n"
                f"Original restored as:\n{source}\n\n"
                f"Encoded output permanently removed:\n{output}\n\n"
                "This cannot be undone by MKV Encoder Q.", parent=self):
            return
        try:
            _restore_backup_transaction(source, output, backup)
        except OSError as exc:
            messagebox.showerror(
                "Could not restore backup",
                f"No completed restore was left in place.\n\n{exc}", parent=self)
            return
        self.completed_outputs.pop(source, None)
        self.deleted_backups.discard(source)
        self.force_encode_paths.discard(source)
        self.clean_paths.discard(source)
        self.file_selection[source] = False
        if self.live is not None:
            self.live.mark_reverted(source)
        iid = str(source)
        if self.table.exists(iid):
            values = list(self.table.item(iid, "values"))
            values[1] = _force_text(False)
            values[3] = "Reverted"
            values[4] = "—"
            values[5] = "—"
            values[7] = "—"
            values[8] = "Original restored from backup"
            values[9] = _row_action_text("Reverted")
            values[10] = _encode_text(False)
            values[15] = "Restored"
            self.table.item(iid, values=values, tags=())
        self._refresh_encode_all_heading()
        self._refresh_force_all_heading()
        self._log(f"Restored original from backup: {source}")

    def _set_file_forced(self, iid: str, path: Path, enabled: bool,
                         values: list | None = None,
                         refresh_heading: bool = True) -> bool:
        """Change force state without changing the row's visible position."""
        values = values or list(self.table.item(iid, "values"))
        if not enabled and path in self.queued_force:
            original, _index = self.queued_force[path]
            if self.live is None or not self.live.cancel_reanalysis(
                    path, str(original[3]), str(original[8])):
                return False
            self.live.set_force_encode(path, False)
            self.force_encode_paths.discard(path)
            self.table.item(iid, values=original)
            del self.queued_force[path]
            if refresh_heading:
                self._refresh_force_all_heading()
            return True
        if enabled:
            self.force_encode_paths.add(path)
        else:
            self.force_encode_paths.discard(path)
        if self.live is not None:
            if not self.live.set_force_encode(path, enabled):
                return False
            if enabled and str(values[3]) in {
                    "Skipped", "Failed", "Cancelled", "Reverted"}:
                original = list(values)
                index = self.table.index(iid)
                if self.live.queue_reanalysis(path):
                    self.queued_force[path] = (original, index)
                    values[3] = "Queued"
                    values[9] = "—"
        values[1] = _force_text(enabled)
        values[8] = ("Queued — forced encode; sampling will be skipped"
                     if enabled and path in self.queued_force
                     else "Forced encode — sampling will be skipped" if enabled
                     else "Waiting for analysis")
        self.table.item(iid, values=values)
        if refresh_heading:
            self._refresh_force_all_heading()
        return True

    def reanalyze(self, target: Path) -> None:
        """Rerun sampling for one unfinished file using the current settings."""
        target = target.resolve()
        if not self.table.exists(str(target)):
            return
        try:
            config = self.run_config if self.running else load_config(None)
        except ValueError:
            return
        progressive = config.reanalysis_mode == "progressive"
        cached = self.progressive_samples.get(target)
        cached_count = (len(cached[1]) if cached and
                        cached[0] == _sampling_signature(config) else 0)
        if progressive and cached_count >= config.progressive_sample_max:
            values = list(self.table.item(str(target), "values"))
            values[9] = _progressive_action_text(
                cached_count, config.progressive_sample_max)
            self.table.item(str(target), values=values)
            return
        if progressive:
            self.progressive_targets[target] = min(
                config.progressive_sample_max,
                max(config.sample_count, cached_count + 1))
        if self.running:
            if self.live is None:
                return
            if target in self.queued_reanalysis:
                original, index = self.queued_reanalysis[target]
                if self.live.cancel_reanalysis(
                        target, str(original[3]), str(original[8])):
                    self.table.item(str(target), values=original)
                    del self.queued_reanalysis[target]
                    self.progressive_targets.pop(target, None)
                return
            values = list(self.table.item(str(target), "values"))
            self.force_encode_paths.discard(target)
            self.live.set_force_encode(target, False)
            values[1] = _force_text(False)
            if not self.live.queue_reanalysis(target):
                return
            if not progressive:
                self.sample_variants[target] = self.sample_variants.get(target, 0) + 1
            index = self.table.index(str(target))
            self.queued_reanalysis[target] = (values, index)
            values[3] = "Queued"
            values[8] = "Queued for reanalysis"
            values[9] = (_progressive_action_text(
                cached_count, config.progressive_sample_max, queued=True)
                if progressive else _reanalyze_action_text(queued=True))
            self.table.item(str(target), values=values)
            return
        preserve: set[Path] = set()
        for iid in self.table.get_children():
            path = Path(iid).resolve()
            if path != target:
                preserve.add(path)
        self.reanalyze_target = target
        if not progressive:
            self.sample_variants[target] = self.sample_variants.get(target, 0) + 1
        self.reanalyze_preserve = preserve
        self.force_encode_paths.discard(target)
        values = list(self.table.item(str(target), "values"))
        values[1] = _force_text(False)
        values[3:9] = ["Waiting", "—", "—", values[6], "—",
                       "Queued for reanalysis"]
        values[9] = (_progressive_action_text(
            cached_count, config.progressive_sample_max, active=True)
            if progressive else _reanalyze_action_text(active=True))
        self.table.item(str(target), values=values)
        self.start()
        if not self.running:
            self.reanalyze_target = None
            self.reanalyze_preserve.clear()
            self.progressive_targets.pop(target, None)
            values[9] = _reanalyze_action_text()
            self.table.item(str(target), values=values)

    def _emit(self, *event) -> None:
        kind = event[0] if event else ""
        urgent = kind in {
            "current", "details", "progress", "completed_progress", "pause",
            "encoding_controls", "queue_idle", "stopped",
        }
        if (kind == "row" and len(event) > 2
                and event[2] in {
                    "Sampling", "Encoding", "Paused", "Completed", "Failed",
                    "Skipped", "Cancelled"}):
            urgent = True
        target = getattr(self, "urgent_events", None) if urgent else None
        (target if target is not None else self.events).put(event)

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _continue_pending_start(self) -> None:
        """Resume a deferred Start only if the request was not cancelled."""
        if self.start_pending:
            self.start()

    def _inspection_work_pending(self) -> bool:
        """Return True across every worker, queue, and GUI-handoff phase."""
        if not getattr(self, "inspect_new_files", False):
            return False
        thread = getattr(self, "inspection_thread", None)
        if thread is not None and thread.is_alive():
            return True
        with getattr(self, "inspection_lock", threading.RLock()):
            pending_queue = getattr(self, "inspection_queue", None)
            return bool(
                getattr(self, "inspection_cycle_active", False)
                or getattr(self, "inspection_active_path", None) is not None
                or getattr(self, "inspection_enqueued", set())
                or (pending_queue is not None and not pending_queue.empty()))

    def start(self) -> None:
        if self.running:
            return
        if self.can_start_callback and not self.can_start_callback():
            self.start_pending = False
            self.start_button.configure(
                state="normal", text="▶ Start", style="StartIdle.TButton")
            self._set_control_indicator(None)
            messagebox.showinfo(
                "Cleaner is running",
                "Wait for the Cleaner run to finish before starting Encoder.",
                parent=self)
            return
        discovery_alive = (
            getattr(self, "directory_discovery_active", False)
            or (self.idle_discovery is not None
                and self.idle_discovery.is_alive()))
        quiet_discovery = (
            discovery_alive and getattr(self, "idle_discovery_quiet", False))
        # A periodic background rescan must not block Start.  Its partial
        # snapshot is discarded and encoding uses the last complete inventory.
        if quiet_discovery:
            self.directory_discovery_cancel.set()
        inventory_busy = discovery_alive and not quiet_discovery
        inspection_busy = OptimizerGUI._inspection_work_pending(self)
        other_work_busy = any(
            thread is not None and thread.is_alive()
            for thread in (self.worker, self.watcher))
        if inventory_busy or inspection_busy or other_work_busy:
            self.start_pending = True
            # Start has already been accepted.  Keep the filter snapshot stable
            # while inventory/inspection finishes so the rows shown when the
            # worker starts cannot differ from the user's Start selection.
            self._set_filter_lock_state(True)
            self.start_button.configure(
                state="disabled", text="▶ Starting…", style="StartActive.TButton")
            if hasattr(self, "home_button"):
                self.home_button.configure(state="disabled")
            self.stop_button.configure(
                state="normal", text="■ Stop All", style="StopIdle.TButton")
            self._set_control_indicator("running")
            if inventory_busy:
                self.status_var.set(
                    "Starting… waiting for directory inventory to finish")
            elif inspection_busy:
                self.status_var.set(
                    "Starting… waiting for media inspection to finish")
            else:
                self.status_var.set("Starting… finishing current directory activity")
            self.after(100, self._continue_pending_start)
            return
        try:
            config = load_config(None)
            root = Path(self.input_var.get() or config.input_directory)
            if not root.is_absolute():
                root = application_directory() / root
            root = root.resolve()
            if not root.is_dir():
                raise ValueError(f"Input folder does not exist: {root}")
            base = default_config_path().parent
            output = Path(config.output_directory) if config.output_directory.strip() else root
            if not output.is_absolute():
                output = base / output
            output = output.resolve()
            output.mkdir(parents=True, exist_ok=True)
            temp = Path(config.temp_directory)
            if not temp.is_absolute():
                temp = base / temp
            temp.mkdir(parents=True, exist_ok=True)
            config = replace(config, temp_directory=str(temp))
            hb = resolve_program(config.handbrake_cli)
            probe = resolve_program(config.ffprobe)
            if not hb or not probe:
                raise ValueError("HandBrakeCLI.exe and ffprobe.exe must be beside MKV Encoder Q or configured in config.json")
        except Exception as exc:
            self.start_pending = False
            self._set_filter_lock_state(False)
            self.start_button.configure(
                state="normal", text="▶ Start", style="StartIdle.TButton")
            if hasattr(self, "home_button"):
                self.home_button.configure(state="normal")
            self._set_control_indicator(None)
            messagebox.showerror("Cannot start", str(exc))
            return
        excluded = output if output != root and output.is_relative_to(root) else None
        allowed = ({self.reanalyze_target}
                   if self.reanalyze_target is not None else None)
        if (self.live is None or self.live.root != root
                or self.live.excluded_root != excluded
                or self.live.allowed_paths != allowed):
            previous = self.live
            if previous is None or previous.root != root:
                OptimizerGUI._reset_directory_session(self)
            self.live = LiveQueue(
                root, config.file_ready_seconds, excluded, allowed_paths=allowed,
                default_selected=config.select_new_files_for_encoding)
            self.live.add_manual(list(getattr(self, "dropped_entries", {}).items()))
            if previous is not None and previous.root == root:
                self.live.completed.update(previous.completed)
            else:
                for source, output_path in getattr(
                        self, "completed_outputs", {}).items():
                    for protected in (source, output_path):
                        try:
                            if protected.is_relative_to(root):
                                self.live.completed.add(protected)
                        except (OSError, ValueError):
                            continue
        self.live.ready_seconds = config.file_ready_seconds
        # A media filter is also an encode boundary.  Hidden rows must never
        # remain eligible merely because they were selected before filtering.
        selected_count = self._sync_visible_selection_for_start()
        self._log(
            f"Start accepted — {selected_count:,} visible file"
            f"{'s' if selected_count != 1 else ''} selected for encoding.")
        self._sync_queue_order()
        self.live.completed.update(self.reanalyze_preserve)
        self.reanalyze_preserve.clear()
        self.running = True
        self.start_pending = False
        self._set_filter_lock_state(True)
        self.stop_event.clear()
        self.scan_now_event.clear()
        self.scan_cycle_complete.clear()
        self.pause_event.clear()
        self.encode_active.clear()
        self.last_file_completed = False
        self.start_button.configure(
            state="disabled", text="▶ Running", style="StartActive.TButton")
        if hasattr(self, "home_button"):
            self.home_button.configure(state="disabled")
        self._set_control_indicator("running")
        self.stop_button.configure(
            state="normal", text="■ Stop All", style="StopIdle.TButton")
        self.pause_button.configure(state="disabled", text="⏸ Pause")
        self.cancel_button.configure(state="disabled")
        self.progress.reset()
        self.status_var.set("Running")
        self.current_var.set("Starting next selected file…")
        self.encode_stats_var.set("")
        self.settings_var.set("")
        self.projection_var.set("")
        self._show_scan_status(self.scan_enabled.is_set())
        self.run_config = config
        self.worker = threading.Thread(target=self._run, args=(root, output, config, hb, probe), daemon=True)
        self.watcher = threading.Thread(target=self._watch, args=(config,), daemon=True)
        self.watcher.start()
        self.worker.start()

    def _watch(self, config: Config) -> None:
        """Discover later directory changes without rebuilding the visible queue.

        The idle inventory has already populated ``self.live`` before Start.
        Rewalking a large NAS tree here used to stat every known file and emit a
        redundant row event for every item just as sampling began.  Thousands
        of Treeview updates could make Tk appear hung even though HandBrake was
        running normally in its worker thread.
        """
        assert self.live is not None
        # The current inventory is the Start snapshot.  With active scanning
        # off there is nothing for a watcher to do; the worker can immediately
        # consume the selected Waiting rows.
        if not self.scan_enabled.is_set():
            self.scan_cycle_complete.set()
            self._emit("scan_activity", False, None)
            return

        # Active scanning looks for changes after the configured interval.  It
        # must never delay the first selected file by repeating idle discovery.
        previous_duration = 0.0
        count = max(
            len(self.live.snapshot()), self.live.last_discovered_count,
            getattr(self, "inventory_files_scanned", 0),
            getattr(self, "inventory_folders_scanned", 0))
        if OptimizerGUI._wait_for_scan_interval(self, _adaptive_rescan_interval(
                config.rescan_interval_seconds, count, previous_duration), count):
            return
        while not self.stop_event.is_set():
            if not self.scan_enabled.is_set():
                self.scan_cycle_complete.set()
                self._emit("scan_activity", False, None)
                return

            before = {
                item.path: (item.status, item.detail, item.size, item.modified_ns)
                for item in self.live.snapshot()
            }
            self._emit("scan_activity", True, None)
            scan_started = time.monotonic()
            encoding = self.encode_active.is_set()
            count = max(
                len(before), self.live.last_discovered_count,
                getattr(self, "inventory_files_scanned", 0),
                getattr(self, "inventory_folders_scanned", 0))
            self.live.refresh_incremental(
                batch_size=25,
                cancelled=self.stop_event.is_set,
                readiness_enabled=not encoding,
                folder_delay_seconds=_background_folder_delay(count, encoding),
                existence_paths=getattr(self, "completed_outputs", {}))
            previous_duration = time.monotonic() - scan_started
            for item in self.live.snapshot():
                current = (item.status, item.detail, item.size, item.modified_ns)
                if before.get(item.path) != current:
                    self._emit("row", item.path, item.status, "—", "—",
                               _size_text(item.size), "—",
                               item.detail)
            self._emit("scan_activity", False, time.strftime("%I:%M:%S %p").lstrip("0"))
            count = max(
                len(self.live.snapshot()), self.live.last_discovered_count,
                getattr(self, "inventory_files_scanned", 0),
                getattr(self, "inventory_folders_scanned", 0))
            if OptimizerGUI._wait_for_scan_interval(self, _adaptive_rescan_interval(
                    config.rescan_interval_seconds, count, previous_duration), count):
                return

    def _wait_for_scan_interval(self, seconds: float, count: int) -> bool:
        """Wait for the schedule, Stop, or the user-visible Scan Now action."""
        manual = getattr(self, "scan_now_event", None)
        if manual is None:
            return self.stop_event.wait(seconds)
        self._emit("scan_schedule", seconds, count, self.encode_active.is_set())
        deadline = time.monotonic() + seconds
        while not self.stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if manual.wait(min(0.5, remaining)):
                manual.clear()
                return False
        return True

    def _run(self, root: Path, output: Path, config, hb: str, probe: str) -> None:
        assert self.live is not None
        backup = config.backup_originals
        idle_reported = False
        while not self.stop_event.is_set():
            waiting = self.live.claim_waiting()
            if waiting:
                idle_reported = False
                path = waiting.path
                if self.stop_event.is_set():
                    self.live.set_status(path, "Waiting", "Ready")
                    break
                current_size = _size_text(waiting.size)
                self._emit("row", path, "Sampling", "—", "—", current_size, "—", "Testing benefit")
                self._emit("current", f"Sampling: {path.name}")
                # LiveQueue paths are canonical already.  Avoid turning each
                # file transition into another UNC/network lookup.
                forced = waiting.force_encode or path in self.force_encode_paths
                try:
                    if forced:
                        self._emit("current", f"Preparing forced encode: {path.name}")
                        result = inspect_for_forced_encode(path, config, probe)
                        self._emit("log", f"File: {path.name}")
                        self._emit("log", "Forced encode — sampling and savings analysis skipped")
                    else:
                        progressive_target = None
                        prior_samples = ()
                        if config.reanalysis_mode == "progressive":
                            progressive_target = self.progressive_targets.pop(
                                path, config.sample_count)
                            cached = self.progressive_samples.get(path)
                            if (cached and cached[0] == _sampling_signature(config)
                                    and progressive_target > config.sample_count):
                                prior_samples = cached[1]
                            if prior_samples:
                                self._emit(
                                    "log",
                                    f"Reanalyzing with {progressive_target} samples — "
                                    f"previous analysis used {len(prior_samples)}, "
                                    f"maximum {config.progressive_sample_max}",
                                )
                        result = scan_file(
                            path, config, probe, hb,
                            self.sample_variants.get(path, 0), prior_samples,
                            progressive_target)
                except Exception as exc:
                    logging.exception("GUI analysis failed: %s", path)
                    self.encode_failure_details[path.resolve()] = str(exc)
                    self.live.mark_done(path, "Failed", str(exc))
                    self._emit("row", path, "Failed", "—", "—", current_size,
                               "—", FAILED_DETAILS_TEXT)
                    self._emit("log", f"Failed: {path.name} — {exc}")
                    continue
                if result.media is not None:
                    try:
                        self._emit("inspection_metadata", inspection_from_media(result.media))
                    except Exception:
                        logging.exception("Could not record media inspection: %s", path)
                if (config.reanalysis_mode == "progressive"
                        and result.sample_results):
                    self.progressive_samples[path] = (
                        _sampling_signature(config), result.sample_results)
                savings = (f"{result.projected_savings_percent:.1f}%"
                           if result.projected_savings_percent is not None else "—")
                projected = _size_text(result.projected_size)
                if not forced:
                    self._emit("log", _sampling_filename_text(path))
                for sample in result.sample_results:
                    self._emit(
                        "log",
                        f"Sample {sample.number}/{sample.total} — "
                        f"{_clock_text(sample.start_seconds)} — "
                        f"{_size_text(sample.output_size)} sample — "
                        f"{sample.projected_savings_percent:.1f}% projected savings — "
                        f"{_size_text(sample.projected_size)} projected output",
                    )
                if result.projected_size is not None:
                    elapsed = _duration_text(result.sampling_elapsed_seconds or 0)
                    self._emit(
                        "log",
                        f"Average used for projection — {savings} savings — "
                        f"{projected} projected output — total sampling time {elapsed}",
                    )
                eligible = (result.decision == Decision.RECOMMEND or
                            (forced and result.decision != Decision.ERROR
                             and result.media is not None and result.media.videos))
                if not eligible:
                    status = "Failed" if result.decision == Decision.ERROR else "Skipped"
                    self.live.mark_done(path, status, result.reason)
                    if status == "Failed":
                        self.encode_failure_details[path.resolve()] = result.reason
                    decision = (f"Skip — below {config.minimum_projected_savings_percent:.1f}% limit"
                                if status == "Skipped" else FAILED_DETAILS_TEXT)
                    self._emit("row", path, status, "—", savings, current_size, projected, decision)
                    self._emit("log", f"{status}: {path.name} — {result.reason}")
                    continue
                if self.stop_event.is_set():
                    self.live.set_status(path, "Waiting", "Ready")
                    self._emit("row", path, "Waiting", "—", savings,
                               current_size, projected, "Ready")
                    break
                self.live.set_status(path, "Encoding")
                decision = ("Encode — forced for this file" if forced
                            else f"Encode — meets {config.minimum_projected_savings_percent:.1f}% limit")
                self._emit("row", path, "Encoding", "Working", savings, current_size, projected, decision)
                video = result.media.videos[0] if result.media and result.media.videos else None
                rf_key = "2160" if video and (video.width >= 3000 or video.height >= 1400) else (
                    "1080" if video and (video.width >= 1600 or video.height >= 800) else
                    "720" if video and video.height >= 600 else "480")
                rf = config.quality_rf_by_resolution[rf_key]
                resolution = f"{video.width}×{video.height}" if video else "Unknown resolution"
                audio = ("all streams" if config.audio_stream_mode == "all"
                         else "first English stream"
                         if config.audio_stream_mode == "first_english"
                         else f"{config.audio_stream_mode} stream")
                self._emit("details", path.name,
                           f"H.265 {config.video_bit_depth}-bit • RF {rf:g} • {config.encoder_preset.title()} • {resolution}",
                           f"Projected: {savings} savings • approximately {projected} • Audio: {audio}")
                try:
                    assert result.media is not None
                    started = time.monotonic()
                    self.encode_active.set()
                    file_cancel = threading.Event()
                    self.current_cancel_event = file_cancel
                    self._emit("encoding_controls", True)
                    def on_progress(percent: float, detail: str) -> None:
                        status = "Paused" if self.pause_event.is_set() else "Encoding"
                        self._emit("row", path, status, f"{percent:.1f}%", savings,
                                   current_size, projected, decision)
                        self._emit("progress", percent, f"{detail} • Elapsed {_duration_text(time.monotonic() - started)}")
                    def on_pause(paused: bool) -> None:
                        self._emit("pause", path, paused, savings)
                    source_root = self.live.source_root(path)
                    job_output = output if config.output_directory.strip() else source_root
                    outcome = encode(result.media, source_root, job_output,
                                     config, backup, hb, probe,
                                     file_cancel, on_progress, self.pause_event, on_pause)
                    previous_retained = self.retained_failed_outputs.pop(
                        path.resolve(), None)
                    if previous_retained is not None and previous_retained != outcome.output:
                        try:
                            previous_retained.unlink(missing_ok=True)
                        except OSError:
                            logging.exception(
                                "Could not remove superseded retained output: %s",
                                previous_retained)
                    self.encode_failure_details.pop(path.resolve(), None)
                    self.completed_outputs[path.resolve()] = outcome.output.resolve()
                    if self.output_ready_callback:
                        self.output_ready_callback(path.resolve(), outcome.output.resolve())
                    if _clean_after_encode_requested(
                            self.clean_new_encodes, path, self.clean_paths):
                        self._emit("clean_after_encode", path.resolve())
                    elapsed = _duration_text(time.monotonic() - started)
                    result_text, _larger_output = _completed_result(
                        outcome.bytes_before, outcome.bytes_after, elapsed)
                    detail = f"Completed — {result_text}"
                    self.live.mark_done(path, "Completed", detail)
                    if outcome.output.resolve() != path.resolve():
                        duplicate_output_row = self.live.mark_generated_output(
                            outcome.output)
                        if duplicate_output_row:
                            self._emit("remove_row", outcome.output)
                    self._emit("row", path, "Completed", "100.0%", savings, current_size, projected,
                               result_text)
                    self._emit("completed_progress", *_completed_progress(
                        f"{detail}: {path.name}"))
                    self._emit("log", f"{detail}: {path.name}")
                except EncodeCancelled:
                    self.live.mark_done(path, "Cancelled", "Cancelled by user")
                    self._emit("row", path, "Cancelled", "—", savings, current_size,
                               projected, "Cancelled")
                except BackupAlreadyExists as exc:
                    reason = f"Skip — backup already exists: {exc.backup_path}"
                    self.live.mark_done(path, "Skipped", reason)
                    self._emit("row", path, "Skipped", "—", savings, current_size,
                               projected, reason)
                    self._emit("log", f"Skipped: {path.name} — backup already exists: "
                               f"{exc.backup_path}")
                except KeyboardInterrupt:
                    self.stop_event.set()
                except EncodeVerificationFailed as exc:
                    resolved = path.resolve()
                    self.retained_failed_outputs[resolved] = exc.retained_output
                    self.encode_failure_details[resolved] = str(exc)
                    logging.exception("GUI encode verification failed: %s", path)
                    self.live.mark_done(path, "Failed", str(exc))
                    self._emit("row", path, "Failed", "—", savings, current_size,
                               projected, FAILED_DETAILS_TEXT)
                    self._emit(
                        "log", f"Failed: {path.name} — {exc} — completed output "
                        f"retained at {exc.retained_output}")
                except Exception as exc:
                    logging.exception("GUI encode failed: %s", path)
                    self.encode_failure_details[path.resolve()] = str(exc)
                    self.live.mark_done(path, "Failed", str(exc))
                    self._emit("row", path, "Failed", "—", savings, current_size,
                               projected, FAILED_DETAILS_TEXT)
                    self._emit("log", f"Failed: {path.name} — {exc}")
                finally:
                    self.encode_active.clear()
                    self.current_cancel_event = None
                    self.pause_event.clear()
                    self._emit("encoding_controls", False)
            elif (self.scan_enabled.is_set() and not self.live.has_pending()
                  and not idle_reported):
                self._emit("queue_idle")
                idle_reported = True
            if (not self.scan_enabled.is_set() and self.scan_cycle_complete.is_set()
                    and not self.live.has_pending()):
                break
            self.stop_event.wait(0.25)
        self.stop_event.set()
        self._emit("stopped")

    def stop(self) -> None:
        if not self.running:
            if self.start_pending:
                self.start_pending = False
                self.start_button.configure(
                    state="normal", text="▶ Start", style="StartIdle.TButton")
                if hasattr(self, "home_button"):
                    self.home_button.configure(state="normal")
                self.stop_button.configure(
                    state="disabled", text="■ Stop All", style="StopIdle.TButton")
                self._set_control_indicator(None)
                self._set_filter_lock_state(False)
                self.status_var.set("Pending Start cancelled")
                self.current_var.set("Ready. Choose files and press Start.")
                self._log("Pending Start cancelled by Stop All.")
            return
        if self.has_active_work() and not messagebox.askyesno(
                "Stop MKV Encoder Q",
                "Cancel the current encode and stop the complete session?"):
            return
        self.stop_button.configure(
            state="disabled", text="■ Stopping...", style="StopPending.TButton")
        self._set_control_indicator("stopping")
        self.pause_event.clear()
        self.stop_event.set()
        if self.current_cancel_event is not None:
            self.current_cancel_event.set()
        self.status_var.set("Stopping...")

    def has_active_work(self) -> bool:
        """Return True only while a file is being sampled or encoded."""
        if (self.encode_active.is_set() or self.pause_event.is_set()
                or self.current_cancel_event is not None):
            return True
        if self.live is None:
            return False
        return any(item.status in {"Sampling", "Encoding", "Paused"}
                   for item in self.live.snapshot())

    def _set_control_indicator(self, state: str | None) -> None:
        """Move one immediate acknowledgement marker beneath the active control."""
        states = {
            "running": (3, "RunningIndicator.TLabel"),
            "stopping": (4, "StoppingIndicator.TLabel"),
            "paused": (5, "PausedIndicator.TLabel"),
            "cancelling": (6, "CancellingIndicator.TLabel"),
        }
        selected = states.get(state)
        if selected is None:
            self.control_indicator.grid_remove()
            return
        column, style = selected
        self.control_indicator.configure(style=style)
        self.control_indicator.grid(row=1, column=column, sticky="ew")

    def cancel_file(self) -> None:
        event = self.current_cancel_event
        if event is not None and messagebox.askyesno(
                "Cancel current file", "Cancel the current encode and continue with the queue?"):
            self.pause_event.clear()
            event.set()
            self.cancel_button.configure(
                state="disabled", text="✖ Cancelling...", style="StopPending.TButton")
            self._set_control_indicator("cancelling")
            self.status_var.set("Cancelling current file...")

    def toggle_pause(self) -> None:
        if not self.running:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="⏸ Pause", state="disabled")
            self._set_control_indicator("running")
            self.status_var.set("Resuming HandBrake...")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="▶ Resume", state="disabled")
            self._set_control_indicator("paused")
            self.status_var.set("Pausing HandBrake...")

    def _drain_events(self) -> None:
        refresh_headings = False
        refresh_missing = False
        # A large or cached NAS scan can enqueue thousands of row updates faster
        # than Tk can paint them.  Yield regularly so window messages, redraws,
        # and Cancel remain responsive while the list catches up.
        drain_deadline = time.monotonic() + (
            0.006 if getattr(self, "background_ui", False) else 0.030)
        try:
            while True:
                urgent_events = getattr(self, "urgent_events", None)
                if urgent_events is not None:
                    try:
                        event = urgent_events.get_nowait()
                    except queue.Empty:
                        event = self.events.get_nowait()
                else:
                    event = self.events.get_nowait()
                if event[0] == "idle_discovery_batch":
                    _, discovered_live, items, count, *rest = event
                    acknowledged = rest[0] if rest else None
                    if discovered_live is self.live and not self.running:
                        baseline = getattr(self, "directory_scan_baseline_count", 0)
                        displayed_count = max(baseline, count)
                        self.directory_discovered_count = displayed_count
                        # The batched global refresh updates total/selected
                        # together without repainting this label per file.
                        for item in items:
                            self._emit(
                                "row", item.path, item.status, "—", "—",
                                _size_text(item.size), "—",
                                _inventory_decision(
                                    item,
                                    item.path in self.inspection_results,
                                    getattr(self, "inspect_new_files", False)))
                        # Inventory progress events own the permanent status
                        # line.  A row batch must not erase the quickly found
                        # top-level folder estimate or the running file count.
                    if acknowledged is not None:
                        # Put the acknowledgement after this batch's row events;
                        # discovery cannot outrun the visible Tk list.
                        self._emit("idle_discovery_batch_done", acknowledged)
                elif event[0] == "idle_discovery_batch_done":
                    event[1].set()
                elif event[0] == "idle_inventory_progress":
                    _, progress_live, phase, folders, files, top_level = event
                    if progress_live is self.live and not self.running:
                        self.inventory_folders_scanned = folders
                        self.inventory_files_scanned = files
                        self.inventory_top_level_folders = top_level
                        if phase == "base":
                            self.inventory_status_var.set(
                                f"{top_level:,} top-level folders found — "
                                "building recursive inventory…")
                            self.inventory_progress["value"] = 0
                        elif phase == "recursive":
                            approximate_total = max(top_level, 1)
                            approximate_done = max(folders - 1, 0)
                            self.inventory_progress["value"] = min(
                                approximate_done / approximate_total * 100, 99)
                            self.inventory_status_var.set(
                                f"{approximate_done:,} folders scanned • "
                                f"{top_level:,} top-level folders initially found • "
                                f"{files:,} filesystem files checked • "
                                f"{self.directory_discovered_count:,} videos found")
                elif event[0] == "idle_inventory_ready_batch":
                    _, ready_live, items, acknowledged = event
                    if ready_live is self.live and not self.running:
                        for item in items:
                            inspected = item.path in self.inspection_results
                            self._emit(
                                "row", item.path, item.status, "—", "—",
                                _size_text(item.size), "—",
                                item.detail if item.status == "Missing" else
                                _inventory_decision(
                                    item, inspected,
                                    getattr(self, "inspect_new_files", False)))
                    if acknowledged is not None:
                        self._emit("idle_discovery_batch_done", acknowledged)
                elif event[0] == "idle_discovered":
                    if event[1] is self.live and not self.running:
                        self.directory_discovery_active = False
                        self.idle_discovery_quiet = False
                        inventory_callback = getattr(
                            self, "inventory_state_callback", None)
                        if inventory_callback:
                            inventory_callback(False)
                        self.cancel_inventory_button.configure(state="disabled")
                        count = event[2] if len(event) > 2 else len(self.live.snapshot())
                        folders = event[3] if len(event) > 3 else 0
                        files = event[4] if len(event) > 4 else 0
                        top_level = event[5] if len(event) > 5 else 0
                        quiet_rescan = bool(event[6]) if len(event) > 6 else False
                        if len(event) > 7:
                            self.last_idle_scan_duration = float(event[7])
                        silent_rescan = (
                            quiet_rescan
                            and count == getattr(
                                self, "directory_scan_baseline_count", count))
                        self.directory_discovered_count = count
                        scale = max(count, files, folders)
                        interval = _adaptive_rescan_interval(
                            getattr(self, "scan_configured_interval", 5.0),
                            scale, self.last_idle_scan_duration)
                        self.scan_next_deadline = time.monotonic() + interval
                        inventory_items = self.live.snapshot()
                        current_paths = {
                            item.path for item in inventory_items
                            if item.status != "Missing"
                        }
                        self.inspection_results = {
                            path: result for path, result in self.inspection_results.items()
                            if path in current_paths
                        }
                        self.inspection_errors = {
                            path: error for path, error in self.inspection_errors.items()
                            if path in current_paths
                        }
                        displayed_folders = max(0, folders - 1)
                        if not silent_rescan:
                            self.inventory_status_var.set(
                                f"Complete — {displayed_folders:,} folders • "
                                f"{files:,} total files • "
                                f"{count:,} supported videos")
                            self.inventory_progress["value"] = 100
                        self.inspection_inventory_gate.set()
                        if getattr(self, "inspect_new_files", False):
                            inspected = len(self.inspection_results) + len(self.inspection_errors)
                            failed = len(self.inspection_errors)
                            if not silent_rescan:
                                self.inspection_status_var.set(
                                    (_inspection_summary(count, inspected, failed, "complete")
                                     if inspected >= count else
                                     f"Starting inspection — {inspected:,} of "
                                     f"{count:,} videos already inspected"))
                                self.inspection_progress["value"] = (
                                    inspected / count * 100 if count else 100)
                            self.queue_inspection(
                                item.path for item in inventory_items
                                if item.status != "Missing")
                        elif (not silent_rescan
                              and not getattr(self, "inspect_new_files", False)):
                            self.inspection_status_var.set(
                                _inspection_summary(count, 0, 0, "off"))
                        if not getattr(self, "inspect_new_files", False):
                            self.apply_media_view()
                        self._show_scan_status(
                            self.scan_enabled.is_set(), last_scan=time.strftime(
                                "%I:%M:%S %p").lstrip("0"))
                        refresh_headings = True
                        refresh_missing = True
                elif event[0] == "idle_discovery_cancelled":
                    if event[1] is self.live:
                        self.directory_discovery_active = False
                        self.idle_discovery_quiet = False
                        inventory_callback = getattr(
                            self, "inventory_state_callback", None)
                        if inventory_callback:
                            inventory_callback(False)
                        self.cancel_inventory_button.configure(state="disabled")
                        if hasattr(self, "scan_now_button"):
                            self.scan_now_button.configure(state="normal")
                        self.inspection_inventory_gate.set()
                        self.inventory_status_var.set("Directory inventory cancelled")
                elif event[0] == "idle_scan_error":
                    self.directory_discovery_active = False
                    self.idle_discovery_quiet = False
                    inventory_callback = getattr(
                        self, "inventory_state_callback", None)
                    if inventory_callback:
                        inventory_callback(False)
                    self.cancel_inventory_button.configure(state="disabled")
                    if hasattr(self, "scan_now_button"):
                        self.scan_now_button.configure(state="normal")
                    self.inspection_inventory_gate.set()
                    if not self.running:
                        self.inventory_status_var.set(f"Unavailable: {event[1]}")
                elif event[0] == "row":
                    _, path, status, progress, savings, current_size, projected, decision = event
                    if (status in {"Sampling", "Encoding", "Paused"}
                            and hasattr(self, "start_button")):
                        self.start_button.configure(
                            state="disabled", text="▶ Running",
                            style="StartActive.TButton")
                        self.status_var.set("Paused" if status == "Paused" else "Running")
                        self._set_control_indicator(
                            "paused" if status == "Paused" else "running")
                    iid = str(path)
                    resolved = Path(path)
                    if self.live is None or resolved not in self.live.items:
                        resolved = resolved.resolve()
                    row_existed = self.table.exists(iid)
                    previous_status = (str(self.table.set(iid, "status"))
                                       if row_existed else None)
                    previous_values = (self.table.item(iid, "values")
                                       if row_existed else ())
                    progress, savings, current_size, projected, decision = (
                        _preserve_completed_refresh(
                            previous_values, status, progress, savings,
                            current_size, projected, decision))
                    if resolved in self.queued_reanalysis and status == "Sampling":
                        self.queued_reanalysis.pop(resolved, None)
                    if resolved in self.queued_force and status == "Sampling":
                        self.queued_force.pop(resolved, None)
                    action = _row_action_text(
                        status,
                        reanalysis_queued=(status == "Queued"
                                           and resolved not in self.queued_force),
                        reanalysis_active=(
                            self.reanalyze_target == resolved
                            and status in {"Waiting", "Checking file...", "Sampling"}))
                    config = getattr(self, "run_config", None)
                    cached = getattr(self, "progressive_samples", {}).get(resolved)
                    if (config is not None and config.reanalysis_mode == "progressive"
                            and cached and cached[0] == _sampling_signature(config)
                            and status in {"Skipped", "Failed", "Cancelled", "Reverted"}):
                        action = _progressive_action_text(
                            len(cached[1]), config.progressive_sample_max,
                            queued=(status == "Queued"),
                            active=(self.reanalyze_target == resolved
                                    and status == "Sampling"))
                    resolved_force = resolved in self.force_encode_paths
                    if (status == "Missing" and self.table.exists(iid)
                            and resolved not in self.missing_row_values):
                        self.missing_row_values[resolved] = list(
                            self.table.item(iid, "values"))
                    elif status != "Missing" and resolved in self.missing_row_values:
                        restored_values = self.missing_row_values.pop(resolved)
                        restored_values[2] = path.name
                        restored_values[3] = status
                        restored_values[11] = str(path.parent)
                        self.table.item(iid, values=restored_values, tags=())
                        refresh_headings = True
                        refresh_missing = True
                        if self.status_callback:
                            self.status_callback(resolved, status)
                        continue
                    # FileState is the authoritative inventory/selection
                    # record.  file_selection is only a compatibility mirror
                    # for state that must survive queue reconstruction.
                    if self.live is not None and resolved in self.live.items:
                        selected = self.live.items[resolved].selected
                        self.file_selection[resolved] = selected
                    else:
                        selected = self.file_selection.setdefault(
                            resolved, self.default_encode_selected)
                    missing = status == "Missing"
                    if (not row_existed
                            and getattr(self, "clean_new_encodes", False)
                            and self.clean_available and not missing):
                        self.clean_paths.add(resolved)
                    inspection = getattr(self, "inspection_results", {}).get(resolved)
                    backup_status = (self._backup_status(
                                         resolved, status,
                                         check_disk=status not in {
                                             "Sampling", "Encoding", "Paused"})
                                     if hasattr(self, "_backup_status") else "—")
                    values = ("—" if missing else "☰",
                              "—" if missing else _force_text(resolved_force),
                              OptimizerGUI._marked_filename(self, path), status,
                              progress, savings, current_size, projected, decision, action,
                              "—" if missing else _encode_text(selected), str(path.parent),
                              "—" if missing else _clean_text(
                                  resolved in self.clean_paths, self.clean_available),
                              inspection.media_text if inspection else "—",
                              inspection.rating if inspection else "—",
                              backup_status)
                    if row_existed:
                        self.table.item(
                            iid, values=values, tags=_row_tags(
                                status, "larger than source" in str(decision)))
                    else:
                        self.table.insert(
                            "", "end", iid=iid, values=values,
                            tags=_row_tags(
                                status, "larger than source" in str(decision)))
                    # Apply the current media filter as inventory rows arrive.
                    # With a media-dependent filter, uninspected rows stay
                    # hidden until their inspection result proves they match.
                    filter_one = getattr(self, "_apply_filter_to_path", None)
                    if callable(filter_one):
                        filter_one(resolved, refresh_headings=False)
                    if (not getattr(self, "directory_discovery_active", False)
                            and (not row_existed or previous_status != status)):
                        refresh_headings = True
                        refresh_missing = (
                            not row_existed or previous_status == "Missing"
                            or status == "Missing")
                    if _should_queue_row_inspection(
                            getattr(self, "inspect_new_files", False), missing,
                            getattr(self, "directory_discovery_active", False)):
                        self.queue_inspection([resolved])
                    if self.status_callback:
                        self.status_callback(resolved, status)
                elif event[0] == "inspection_result":
                    _, path, result, error, done, total, failed = event
                    resolved = Path(path)
                    # Keep a path reserved until its result reaches the GUI.
                    # Otherwise an inventory completion arriving between the
                    # worker finishing and this event can enqueue the same
                    # cached path again and keep the inspection cycle alive.
                    with self.inspection_lock:
                        self.inspection_enqueued.discard(resolved)
                    if result is not None:
                        self.inspection_results[resolved] = result
                        self.inspection_errors.pop(resolved, None)
                    else:
                        self.inspection_errors[resolved] = error
                    self._update_row_media(resolved, result, error)
                    filter_one = getattr(self, "_apply_filter_to_path", None)
                    if callable(filter_one):
                        filter_one(resolved, refresh_headings=False)
                    refresh_headings = True
                    inspected = len(self.inspection_results) + len(self.inspection_errors)
                    failed = len(self.inspection_errors)
                    discovered = max(
                        getattr(self, "directory_discovered_count", 0), inspected)
                    percent = inspected / discovered * 100 if discovered else 0
                    self.inspection_progress["value"] = percent
                    elapsed = max(0.0, time.monotonic() - self.inspection_started_at)
                    eta = ((elapsed / inspected) * (discovered - inspected)
                           if inspected else 0)
                    self.inspection_status_var.set(
                        _inspection_summary(
                            discovered, inspected, failed, "running",
                            resolved.name, eta))
                elif event[0] == "clean_after_encode":
                    path = Path(event[1]).resolve()
                    if self.clean_request_callback:
                        self.clean_request_callback([path], switch_tab=False)
                        self._log(f"Queued for Cleaner after encode: {path.name}")
                    self.clean_paths.discard(path)
                    iid = str(path)
                    if self.table.exists(iid):
                        values = list(self.table.item(iid, "values"))
                        if len(values) >= 13:
                            values[12] = _clean_text(False)
                            self.table.item(iid, values=values)
                    self._refresh_clean_all_heading()
                elif event[0] == "inspection_waiting":
                    _, done, total, failed = event
                    self.inspection_status_var.set(
                        _inspection_summary(
                            getattr(self, "directory_discovered_count", total),
                            done, failed, "waiting"))
                elif event[0] == "inspection_metadata":
                    result = event[1]
                    result_path = Path(result.path)
                    if self.live is None or result_path not in self.live.items:
                        result_path = result_path.resolve()
                    self.inspection_results[result_path] = result
                    self.inspection_cache.put(result)
                    self._update_row_media(result.path, result)
                    filter_one = getattr(self, "_apply_filter_to_path", None)
                    if callable(filter_one):
                        filter_one(result_path, refresh_headings=False)
                    refresh_headings = True
                elif event[0] == "inspection_finished":
                    self._finish_inspection_cycle(bool(event[1]))
                elif event[0] == "remove_row":
                    iid = str(event[1])
                    if self.table.exists(iid):
                        self.table.delete(iid)
                    removed = Path(event[1]).resolve()
                    self.clean_paths.discard(removed)
                    self.inspection_results.pop(removed, None)
                    self.inspection_errors.pop(removed, None)
                    self.filtered_rows.discard(iid)
                    self.filter_order = [row for row in self.filter_order if row != iid]
                    refresh_headings = True
                    refresh_missing = True
                elif event[0] == "current":
                    self.current_var.set(event[1])
                    self.encode_stats_var.set("")
                elif event[0] == "details":
                    self.current_var.set(event[1])
                    self.encode_stats_var.set("")
                    self.settings_var.set(event[2])
                    self.projection_var.set(event[3])
                elif event[0] == "progress":
                    self.last_file_completed = False
                    self.progress.set(event[1])
                    self.encode_stats_var.set(event[2])
                elif event[0] == "completed_progress":
                    self.last_file_completed = True
                    self.progress.set(event[1])
                    self.encode_stats_var.set(event[2])
                elif event[0] == "queue_idle":
                    self.last_file_completed = False
                    self.current_var.set("Queue idle — watching for new files")
                    self.encode_stats_var.set("")
                    self.settings_var.set("")
                    self.projection_var.set("")
                    self.progress.set_idle()
                    self.start_button.configure(
                        state="disabled", text="▶ Waiting",
                        style="StartIdle.TButton")
                    self.status_var.set("Watching for new files")
                    self._set_control_indicator(None)
                elif event[0] == "log":
                    self._log(event[1])
                elif event[0] == "pause":
                    _, path, paused, savings = event
                    self.pause_button.configure(state="normal", text="▶ Resume" if paused else "⏸ Pause")
                    self.status_var.set("Paused" if paused else "Running")
                    self._set_control_indicator("paused" if paused else "running")
                    self.current_var.set(("Paused: " if paused else "Encoding: ") + path.name)
                    iid = str(path)
                    if self.table.exists(iid):
                        values = list(self.table.item(iid, "values"))
                        values[3] = "Paused" if paused else "Encoding"
                        self.table.item(
                            iid, values=values,
                            tags=_row_tags("Paused" if paused else "Encoding"))
                elif event[0] == "encoding_controls":
                    active = event[1]
                    self.pause_button.configure(state="normal" if active else "disabled",
                                                text="⏸ Pause")
                    self.cancel_button.configure(
                        state="normal" if active else "disabled",
                        text="✖ Cancel File", style="Cancel.TButton")
                    if active and not self.pause_event.is_set():
                        self._set_control_indicator("running")
                elif event[0] == "scan_schedule":
                    _, seconds, _count, _encoding = event
                    self.scan_next_deadline = time.monotonic() + float(seconds)
                    self._show_scan_status(self.scan_enabled.is_set())
                elif event[0] == "scan_activity":
                    _, scanning, last_scan = event
                    if scanning:
                        self.scan_next_deadline = 0.0
                    self._show_scan_status(
                        self.scan_enabled.is_set(), scanning, last_scan)
                elif event[0] == "stopped":
                    self.running = False
                    self._set_filter_lock_state(False)
                    self.start_button.configure(
                        state="normal", text="▶ Start", style="StartIdle.TButton")
                    if hasattr(self, "home_button"):
                        self.home_button.configure(state="normal")
                    self._set_control_indicator(None)
                    self.reanalyze_target = None
                    for path, (original, index) in sorted(
                            self.queued_reanalysis.items(),
                            key=lambda item: item[1][1]):
                        if self.live is not None:
                            self.live.cancel_reanalysis(path, str(original[3]), str(original[8]))
                        iid = str(path)
                        if self.table.exists(iid):
                            self.table.item(iid, values=original)
                    self.queued_reanalysis.clear()
                    for path, (original, index) in sorted(
                            self.queued_force.items(),
                            key=lambda item: item[1][1]):
                        if self.live is not None:
                            self.live.cancel_reanalysis(path, str(original[3]), str(original[8]))
                            self.live.set_force_encode(path, False)
                        iid = str(path)
                        self.force_encode_paths.discard(path)
                        if self.table.exists(iid):
                            self.table.item(iid, values=original)
                    self.queued_force.clear()
                    for iid in self.table.get_children():
                        values = list(self.table.item(iid, "values"))
                        if len(values) >= 13:
                            values[9] = _row_action_text(str(values[3]))
                            self.table.item(iid, values=values)
                    self._refresh_force_all_heading()
                    self.stop_button.configure(
                        state="disabled", text="■ Stop All", style="StopIdle.TButton")
                    self.pause_event.clear()
                    self.pause_button.configure(state="disabled", text="⏸ Pause")
                    self.cancel_button.configure(
                        state="disabled", text="✖ Cancel File", style="Cancel.TButton")
                    self.status_var.set("Stopped")
                    self._show_scan_status(self.scan_enabled.is_set())
                    self.current_var.set("Stopped. Originals remain protected.")
                    self.settings_var.set("")
                    self.projection_var.set("")
                    if not self.last_file_completed:
                        self.progress.reset()
                        self.encode_stats_var.set("")
                if time.monotonic() >= drain_deadline:
                    break
        except queue.Empty:
            pass
        except Exception:
            # If one display event is stale or malformed, keep scheduling the
            # GUI pump. HandBrake may still be running normally in its worker.
            logging.exception("Could not apply an Encoder interface update")
        self.global_refresh_pending = (
            getattr(self, "global_refresh_pending", False) or refresh_headings)
        self.missing_refresh_pending = (
            getattr(self, "missing_refresh_pending", False) or refresh_missing)
        now = time.monotonic()
        scan_enabled = getattr(self, "scan_enabled", None)
        if (scan_enabled is not None and scan_enabled.is_set()
                and now - getattr(self, "last_scan_status_refresh", 0.0) >= 30.0):
            self.last_scan_status_refresh = now
            self._show_scan_status(self.scan_enabled.is_set())
        refresh_due = (time.monotonic()
                       - getattr(self, "last_global_refresh", 0.0) >= 0.5)
        urgent_events = getattr(self, "urgent_events", None)
        queue_caught_up = (self.events.empty()
                           and (urgent_events is None or urgent_events.empty()))
        if self.global_refresh_pending and (refresh_due or queue_caught_up):
            self._refresh_encode_all_heading()
            self._refresh_force_all_heading()
            self._refresh_clean_all_heading()
            self.global_refresh_pending = False
            self.last_global_refresh = time.monotonic()
        if self.missing_refresh_pending and (refresh_due or queue_caught_up):
            self._refresh_missing_button()
            self.missing_refresh_pending = False
        self.after(100, self._drain_events)

    def _close(self) -> None:
        if self.has_active_work():
            messagebox.showinfo(
                "Work in progress",
                "Wait for sampling or encoding to finish, or stop it before closing.",
                parent=self,
            )
            return
        self.stop_event.set()
        self.directory_discovery_cancel.set()
        self.inspection_cancel.set()
        self.winfo_toplevel().destroy()


def main() -> int:
    root = tk.Tk()
    root.title(f"MKV Encoder Q {__version__}")
    root.geometry("1280x780")
    root.minsize(850, 560)
    app = OptimizerGUI(root)
    app.pack(fill="both", expand=True)
    root.protocol("WM_DELETE_WINDOW", app._close)
    root.mainloop()
    return 0
