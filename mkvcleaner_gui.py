from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from mkvcleaner_core.config import CleanerConfig, load_config, save_config
from mkvcleaner_core.cleaner import can_use_mkvpropedit
from mkvcleaner_core.logger import RunLogger
from mkvcleaner_core.models import MediaFile
from mkvcleaner_core.paths import application_directory, locate_mkvmerge, locate_mkvpropedit
from mkvcleaner_core.process import run_tool
from mkvcleaner_core.scanner import scan_iter
from mkvcleaner_core.workers import process_files
from video_optimizer import __version__
from video_optimizer.drop import expand_dropped_paths
from video_optimizer.gui import _moved_row_order


COLORS = {
    "dark": {
        "bg": "#171a1f", "panel": "#20242b", "field": "#292e37",
        "text": "#e7eaf0", "muted": "#9da6b3", "border": "#353b46",
        "accent": "#27b3d6", "select": "#164e63",
    },
    "light": {
        "bg": "#f3f5f7", "panel": "#ffffff", "field": "#ffffff",
        "text": "#1d2430", "muted": "#667085", "border": "#cfd5dd",
        "accent": "#1689a8", "select": "#ccecf4",
    },
}

STATUS_COLORS = {
    "Queued": "#e6b450", "Waiting for Encode": "#e6b450", "Not Cleaned": "#ff6b6b",
    "Waiting": "#9da6b3", "Inspecting": "#55c7e8", "Will clean": "#55c7e8",
    "Will remux": "#e6b450",
    "Cleaning": "#4da3ff", "Creating backup": "#55c7e8",
    "Editing metadata": "#4da3ff", "Remuxing": "#4da3ff",
    "Replacing original": "#55c7e8", "Verifying": "#c58af9",
    "Done": "#50c878", "Skipped": "#e6b450", "Failed": "#ff6b6b",
    "Cancelled": "#e6b450", "Dry run complete": "#55c7e8",
    "Waiting (paused)": "#e6b450",
    "Discovering": "#55c7e8", "Inspecting": "#55c7e8",
}

CLEANER_ACTIVE_STATUSES = {
    "Waiting", "Inspecting", "Will clean", "Will remux", "Cleaning",
    "Creating backup", "Editing metadata", "Remuxing", "Replacing original",
    "Verifying", "Waiting (paused)", "Discovering",
}


def _cleaner_play_path(path: Path, status: str) -> Path | None:
    """Return a Cleaner row's media path unless that file is being changed."""
    if status in CLEANER_ACTIVE_STATUSES:
        return None
    return path if path.is_file() else None


def plan_filename_replacements(
        paths: list[Path], find_text: str, replacement: str) -> dict[Path, Path]:
    """Validate a literal multi-file filename replacement before changing files."""
    if not find_text:
        raise ValueError("Enter text to find in the selected filenames.")
    plan: dict[Path, Path] = {}
    destinations: set[Path] = set()
    for source in paths:
        new_name = source.name.replace(find_text, replacement)
        if new_name == source.name:
            continue
        if not new_name.strip() or Path(new_name).name != new_name:
            raise ValueError(f"The replacement creates an invalid filename: {new_name}")
        destination = source.with_name(new_name)
        if destination.suffix.lower() != ".mkv":
            raise ValueError(f"The renamed file must remain an MKV file: {new_name}")
        if destination in destinations:
            raise ValueError(f"More than one file would become: {new_name}")
        if destination.exists() and destination != source:
            raise ValueError(f"A file already exists with this name: {new_name}")
        destinations.add(destination)
        plan[source] = destination
    if not plan:
        raise ValueError("The find text was not present in the selected filenames.")
    return plan

REPOSITORY_URL = "https://github.com/jsdeprey/MKVCleaner"
TAG_CLEANUP_LABELS = {
    "all": "Remove all tags — fast",
    "global_only": "Remove global tags only — fast",
    "track_only": "Remove track tags only — may require remuxing",
    "none": "Keep all tags",
}
TAG_CLEANUP_VALUES = {label: value for value, label in TAG_CLEANUP_LABELS.items()}


def _format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


class AboutDialog(tk.Toplevel):
    def __init__(self, parent: "CleanerGUI") -> None:
        super().__init__(parent)
        self.title("About MKVCleaner")
        self.geometry("520x380")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="MKVCleaner", style="AboutTitle.TLabel",
        ).pack(pady=(4, 2))
        ttk.Label(frame, text=f"Version {__version__}").pack()
        ttk.Label(
            frame,
            text="Batch queue to clean selected MKV metadata safely with MKVToolNix",
            justify="center", wraplength=430,
        ).pack(pady=(18, 20))

        ttk.Button(
            frame, text="GitHub Repository",
            command=lambda: webbrowser.open(REPOSITORY_URL),
        ).pack(fill="x", pady=3)
        ttk.Button(
            frame, text="View Releases",
            command=lambda: webbrowser.open(f"{REPOSITORY_URL}/releases"),
        ).pack(fill="x", pady=3)
        ttk.Button(
            frame, text="Report an Issue",
            command=lambda: webbrowser.open(f"{REPOSITORY_URL}/issues/new"),
        ).pack(fill="x", pady=3)
        ttk.Button(
            frame, text="MKVToolNix Website",
            command=lambda: webbrowser.open("https://mkvtoolnix.download/"),
        ).pack(fill="x", pady=3)
        ttk.Label(frame, text="License: MIT", style="Muted.TLabel").pack(
            pady=(15, 4),
        )
        ttk.Button(frame, text="Close", command=self.destroy).pack(pady=(8, 0))


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: "CleanerGUI", config: CleanerConfig) -> None:
        super().__init__(parent)
        self.parent = parent
        self.config = config
        self.vars: dict[str, tk.Variable] = {}
        self.tool_status = tk.StringVar(value="Checking required tools…")
        self.title("MKVCleaner Settings")
        self.geometry("720x590")
        self.minsize(650, 520)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self._build()
        self._update_tool_status()

    def _string(self, name: str, value: object) -> tk.StringVar:
        var = tk.StringVar(value="" if value is None else str(value))
        self.vars[name] = var
        return var

    def _boolean(self, name: str, value: bool) -> tk.BooleanVar:
        var = tk.BooleanVar(value=value)
        self.vars[name] = var
        return var

    def _tab(self, notebook: ttk.Notebook, title: str) -> ttk.Frame:
        frame = ttk.Frame(notebook, padding=14)
        frame.columnconfigure(1, weight=1)
        notebook.add(frame, text=title)
        return frame

    def _row(self, frame: ttk.Frame, row: int, label: str, name: str,
             value: object, choices: tuple[str, ...] | None = None,
             browse: str | None = None):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=7)
        var = self._string(name, value)
        if choices:
            widget = ttk.Combobox(frame, textvariable=var, values=choices, state="readonly")
        else:
            widget = ttk.Entry(frame, textvariable=var)
        widget.grid(row=row, column=1, sticky="ew", padx=6, pady=7)
        if browse:
            ttk.Button(
                frame, text="Browse...",
                command=lambda: self._browse(var, browse),
            ).grid(row=row, column=2, padx=6, pady=7)
        return widget

    def _check(self, frame: ttk.Frame, row: int, label: str,
               name: str, value: bool) -> None:
        ttk.Checkbutton(
            frame, text=label, variable=self._boolean(name, value),
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=6, pady=7)

    def _build(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        c = self.config

        processing = self._tab(notebook, "Folders & processing")
        self._row(processing, 0, "Default input folder", "default_input_directory",
                  c.default_input_directory, browse="folder")
        self._row(processing, 1, "Inspection mode", "inspection_mode",
                  c.inspection_mode, ("stream", "full"))
        self._row(processing, 2, "Worker count", "threads", c.threads,
                  tuple(str(value) for value in range(1, 9)))
        self._check(processing, 3, "Scan subfolders recursively", "recursive", c.recursive)
        ttk.Label(
            processing,
            text="Stream mode queues each file after inspection. Full mode inspects all files first.",
            wraplength=540,
        ).grid(row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(10, 6))

        cleanup = self._tab(notebook, "Cleanup")
        cleanup_items = (
            ("Remove segment title", "remove_title", c.remove_title),
            ("Remove video track names", "remove_video_track_names",
             c.remove_video_track_names),
            ("Remove audio track names", "remove_audio_track_names",
             c.remove_audio_track_names),
            ("Remove subtitle track names", "remove_subtitle_track_names",
             c.remove_subtitle_track_names),
            ("Disable default subtitles unless audio is marked foreign",
             "disable_default_subtitles_for_english_audio",
             c.disable_default_subtitles_for_english_audio),
            ("Clear Default flag from all subtitle tracks",
             "clear_all_subtitle_default_flags",
             c.clear_all_subtitle_default_flags),
            ("Clear Forced flag from all subtitle tracks",
             "clear_all_subtitle_forced_flags",
             c.clear_all_subtitle_forced_flags),
            ("Remove chapters", "remove_chapters", c.remove_chapters),
            ("Remove attachments", "remove_attachments", c.remove_attachments),
        )
        for row, item in enumerate(cleanup_items):
            self._check(cleanup, row, *item)
        ttk.Separator(cleanup).grid(
            row=9, column=0, columnspan=3, sticky="ew", padx=6, pady=(12, 8),
        )
        tag_combo = self._row(
            cleanup, 10, "Tag cleanup", "tag_cleanup",
            TAG_CLEANUP_LABELS[c.tag_cleanup],
            tuple(TAG_CLEANUP_LABELS.values()),
        )
        self.tag_warning = ttk.Label(
            cleanup, text="", wraplength=550, style="Warning.TLabel",
        )
        self.tag_warning.grid(
            row=11, column=0, columnspan=3, sticky="w", padx=6, pady=(6, 0),
        )
        tag_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_tag_warning())
        self._update_tag_warning()

        safety = self._tab(notebook, "Safety")
        self._check(safety, 0, "Keep a .bak copy of the original", "keep_backup", c.keep_backup)
        self._check(safety, 1, "Verify the cleaned file", "verify_output", c.verify_output)
        self._check(safety, 2, "Preserve original timestamps", "preserve_timestamps", c.preserve_timestamps)
        self._check(safety, 3, "Dry run (inspect without changing files)", "dry_run", c.dry_run)

        display = self._tab(notebook, "Display & logging")
        self._row(display, 0, "Log folder", "log_directory", c.log_directory, browse="folder")
        self._check(display, 1, "Create JSON logs", "json_logging", c.json_logging)
        self._check(display, 2, "Show skipped-file details", "show_skipped", c.show_skipped)
        self._row(display, 3, "Color mode", "theme", c.theme, ("dark", "light"))

        tools = self._tab(notebook, "Tools")
        self._row(tools, 0, "MKVToolNix folder", "mkvmerge_path",
                  c.mkvmerge_path or "", browse="folder")
        ttk.Label(
            tools,
            text=(
                "MKVCleaner uses mkvmerge.exe and mkvpropedit.exe from this folder. "
                "Both programs are included with MKVToolNix. Leave blank for automatic detection."
            ),
            wraplength=540,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(10, 6))
        tool_buttons = ttk.Frame(tools)
        tool_buttons.grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=8)
        ttk.Button(tool_buttons, text="Test Detection", command=self._test_tools).pack(
            side="left",
        )
        ttk.Button(
            tool_buttons, text="Download MKVToolNix…",
            command=lambda: webbrowser.open("https://mkvtoolnix.download/downloads.html"),
        ).pack(side="left", padx=(8, 0))
        ttk.Label(tools, textvariable=self.tool_status, wraplength=540).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=6, pady=8)

        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Open JSON (Advanced)", command=self._open_json).pack(side="left")
        ttk.Button(buttons, text="Restore Defaults", command=self._restore_defaults).pack(
            side="left", padx=8,
        )
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=self._save).pack(side="right")

    def _browse(self, var: tk.StringVar, kind: str) -> None:
        if kind == "file":
            selected = filedialog.askopenfilename(
                parent=self, title="Choose mkvmerge",
                filetypes=(("mkvmerge", "mkvmerge.exe"), ("All files", "*.*")),
            )
        else:
            selected = filedialog.askdirectory(parent=self, title="Choose folder")
        if selected:
            var.set(selected)

    def _update_tag_warning(self) -> None:
        selected = str(self.vars["tag_cleanup"].get())
        if TAG_CLEANUP_VALUES.get(selected) == "track_only":
            self.tag_warning.configure(
                text=(
                    "⚠ Files containing both global and track tags must be completely "
                    "remuxed to preserve the global tags. This can take much longer. "
                    "Video, audio, and subtitles are not re-encoded."
                )
            )
        else:
            self.tag_warning.configure(
                text=(
                    "Most selected cleanup operations use fast in-place editing. "
                    "Video, audio, and subtitles are not re-encoded."
                )
            )

    def _test_tools(self) -> None:
        try:
            mkvmerge = locate_mkvmerge(str(self.vars["mkvmerge_path"].get()).strip() or None)
            mkvpropedit = locate_mkvpropedit(mkvmerge)
            merge_version = (run_tool([str(mkvmerge), "--version"]).stdout.splitlines() or ["Found"])[0]
            edit_version = (run_tool([str(mkvpropedit), "--version"]).stdout.splitlines() or ["Found"])[0]
            messagebox.showinfo(
                "MKVToolNix Found",
                f"mkvmerge:\n{mkvmerge}\n{merge_version}\n\n"
                f"mkvpropedit:\n{mkvpropedit}\n{edit_version}\n\nStatus: Both programs found",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror("MKVToolNix Not Found", str(exc), parent=self)
        self._update_tool_status()

    def _update_tool_status(self) -> None:
        try:
            mkvmerge = locate_mkvmerge(
                str(self.vars["mkvmerge_path"].get()).strip() or None)
            try:
                mkvpropedit = locate_mkvpropedit(mkvmerge)
                propedit_status = f"Found — {mkvpropedit}"
            except Exception:
                propedit_status = "Not Found"
            self.tool_status.set(
                f"mkvmerge.exe: Found — {mkvmerge}\n"
                f"mkvpropedit.exe: {propedit_status}")
        except Exception:
            self.tool_status.set(
                "mkvmerge.exe: Not Found\n"
                "mkvpropedit.exe: Not Found")

    def _open_json(self) -> None:
        try:
            os.startfile(self.parent.config_path)  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Cannot open configuration", str(exc), parent=self)

    def _restore_defaults(self) -> None:
        if messagebox.askyesno(
            "Restore Defaults", "Replace every setting with its default value?",
            parent=self,
        ):
            self.destroy()
            SettingsDialog(self.parent, CleanerConfig())

    def _save(self) -> None:
        try:
            get = lambda name: self.vars[name].get()
            updated = replace(
                self.config,
                default_input_directory=str(get("default_input_directory")).strip(),
                inspection_mode=str(get("inspection_mode")),
                threads=int(get("threads")),
                recursive=bool(get("recursive")),
                remove_title=bool(get("remove_title")),
                remove_video_track_names=bool(get("remove_video_track_names")),
                remove_audio_track_names=bool(get("remove_audio_track_names")),
                remove_subtitle_track_names=bool(get("remove_subtitle_track_names")),
                disable_default_subtitles_for_english_audio=bool(
                    get("disable_default_subtitles_for_english_audio")),
                clear_all_subtitle_default_flags=bool(
                    get("clear_all_subtitle_default_flags")),
                clear_all_subtitle_forced_flags=bool(
                    get("clear_all_subtitle_forced_flags")),
                tag_cleanup=TAG_CLEANUP_VALUES[str(get("tag_cleanup"))],
                remove_chapters=bool(get("remove_chapters")),
                remove_attachments=bool(get("remove_attachments")),
                keep_backup=bool(get("keep_backup")),
                verify_output=bool(get("verify_output")),
                preserve_timestamps=bool(get("preserve_timestamps")),
                dry_run=bool(get("dry_run")),
                log_directory=str(get("log_directory")).strip(),
                json_logging=bool(get("json_logging")),
                show_skipped=bool(get("show_skipped")),
                theme=str(get("theme")),
                mkvmerge_path=str(get("mkvmerge_path")).strip() or None,
            )
            save_config(updated, self.parent.config_path)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Invalid Setting", str(exc), parent=self)
            return
        self.parent.config = updated
        if not self.parent.input_var.get().strip():
            self.parent.input_var.set(updated.default_input_directory)
        self.parent.apply_theme(updated.theme)
        messagebox.showinfo(
            "Settings Saved",
            "Settings were saved to cleaner_config.json.\n"
            "They apply the next time Start is pressed.",
            parent=self,
        )
        self.destroy()


class CleanerGUI(tk.Frame):
    def __init__(self, master=None, finished_callback=None,
                 can_start_callback=None, tool_availability_callback=None,
                 rename_guard_callback=None, queue_remove_callback=None,
                 start_guard_callback=None,
                 rename_request_callback=None,
                 rename_directory_callback=None) -> None:
        super().__init__(master)
        self.finished_callback = finished_callback
        self.can_start_callback = can_start_callback
        self.tool_availability_callback = tool_availability_callback
        self.rename_guard_callback = rename_guard_callback
        self.queue_remove_callback = queue_remove_callback
        self.start_guard_callback = start_guard_callback
        self.rename_request_callback = rename_request_callback
        self.rename_directory_callback = rename_directory_callback
        self.config_path = application_directory() / "cleaner_config.json"
        try:
            self.config = load_config(self.config_path)
        except ValueError as exc:
            messagebox.showerror("Invalid config.json", str(exc))
            self.config = CleanerConfig()

        self.events: queue.Queue[tuple] = queue.Queue()
        self.run_event = threading.Event()
        self.stop_event = threading.Event()
        self.running = False
        self.tools_available = False
        self.paused = False
        self.started_at = 0.0
        self.total = 0
        self.discovery_active = False
        self.inspected = 0
        self.completed = 0
        self.counts = {"Cleaned": 0, "Skipped": 0, "Failed": 0, "Dry-run": 0, "Cancelled": 0}
        self.workers: dict[int, tuple[str, str]] = {}
        self.row_ids: dict[Path, str] = {}
        self.queued_row_ids: dict[Path, str] = {}
        self.external_queue: dict[Path, tuple[str, str]] = {}
        self.dropped_queue: dict[Path, tuple[str, str]] = {}
        self.input_var = tk.StringVar(value=self.config.default_input_directory)
        self.activity_file = tk.StringVar(value="Ready")
        self.activity_action = tk.StringVar(value="Choose an input folder and press Start.")
        self.progress_text = tk.StringVar(value="0.0%")
        self.counter_text = tk.StringVar(value="0/0 files")
        self.elapsed_text = tk.StringVar(value="Elapsed 0:00:00")
        self.tool_availability_text = tk.StringVar(value="Checking for MKVToolNix…")
        self.remove_video_names_var = tk.BooleanVar(
            value=self.config.remove_video_track_names)
        self.remove_audio_names_var = tk.BooleanVar(
            value=self.config.remove_audio_track_names)
        self.remove_subtitle_names_var = tk.BooleanVar(
            value=self.config.remove_subtitle_track_names)
        self.disable_default_subtitles_var = tk.BooleanVar(
            value=self.config.disable_default_subtitles_for_english_audio)
        self.clear_subtitle_default_var = tk.BooleanVar(
            value=self.config.clear_all_subtitle_default_flags)
        self.clear_subtitle_forced_var = tk.BooleanVar(
            value=self.config.clear_all_subtitle_forced_flags)
        self.summary_vars = {
            key: tk.StringVar(value="0")
            for key in ("Found", "Inspected", "Cleaned", "Skipped", "Failed")
        }
        self._build()
        self.apply_theme(self.config.theme)
        self.refresh_tool_availability()
        self.after(100, self._drain_events)
        self.after(500, self._tick)

    def _section(self, parent, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=title, padding=9)

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=(12, 10))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Input folder").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.input_var).pack(
            side="left", fill="x", expand=True, padx=8,
        )
        ttk.Button(toolbar, text="Browse...", command=self._browse).pack(side="left")
        self.settings_button = ttk.Button(toolbar, text="Settings", command=self._settings)
        self.settings_button.pack(side="left", padx=(8, 0))
        self.start_button = ttk.Button(
            toolbar, text="Start", style="Accent.TButton", command=self._start,
        )
        self.start_button.pack(side="left", padx=(8, 0))

        self.tool_availability_label = ttk.Label(
            self, textvariable=self.tool_availability_text,
            style="ToolMissing.TLabel", padding=(12, 6),
        )
        self.tool_availability_label.pack(fill="x", padx=12, pady=(0, 8))

        quick_cleanup = ttk.Frame(self, padding=(12, 0, 12, 8))
        quick_cleanup.pack(fill="x")
        self.run_option_checks = []
        ttk.Label(quick_cleanup, text="Remove track names:",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        self.track_name_checks = []
        for label, variable, option in (
                ("Video", self.remove_video_names_var, "video"),
                ("Audio", self.remove_audio_names_var, "audio"),
                ("Subtitles", self.remove_subtitle_names_var, "subtitle")):
            check = ttk.Checkbutton(
                quick_cleanup, text=label, variable=variable,
                command=lambda kind=option: self._set_track_name_option(kind))
            check.pack(side="left", padx=(12, 0))
            self.track_name_checks.append(check)
            self.run_option_checks.append(check)
        self.default_subtitle_check = ttk.Checkbutton(
            quick_cleanup,
            text="Subtitles off unless audio is marked foreign",
            variable=self.disable_default_subtitles_var,
            command=self._set_default_subtitle_option)
        self.default_subtitle_check.pack(side="left", padx=(18, 0))
        self.run_option_checks.append(self.default_subtitle_check)
        ttk.Label(
            quick_cleanup,
            text="This run only; defaults are set in Cleaner Settings.",
            style="Muted.TLabel").pack(side="left", padx=(18, 0))

        subtitle_flags = ttk.Frame(self, padding=(12, 0, 12, 8))
        subtitle_flags.pack(fill="x")
        ttk.Label(subtitle_flags, text="Clear subtitle flags:",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        self.clear_subtitle_default_check = ttk.Checkbutton(
            subtitle_flags, text="Default", variable=self.clear_subtitle_default_var,
            command=self._set_subtitle_flag_option)
        self.clear_subtitle_default_check.pack(side="left", padx=(12, 0))
        self.clear_subtitle_forced_check = ttk.Checkbutton(
            subtitle_flags, text="Forced", variable=self.clear_subtitle_forced_var,
            command=self._set_subtitle_flag_option)
        self.clear_subtitle_forced_check.pack(side="left", padx=(12, 0))
        self.run_option_checks.extend((self.clear_subtitle_default_check,
                                       self.clear_subtitle_forced_check))
        ttk.Label(
            subtitle_flags,
            text="Applies to every subtitle track for this run.",
            style="Muted.TLabel").pack(side="left", padx=(18, 0))

        files_frame = self._section(self, "Found Files")
        files_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        file_actions = ttk.Frame(files_frame)
        file_actions.pack(fill="x", pady=(0, 6))
        self.remove_all_button = ttk.Button(
            file_actions, text="Remove All", command=self._remove_all)
        self.remove_all_button.pack(side="right")
        self.clean_all_button = ttk.Button(
            file_actions, text="Clean All", command=self._clean_all_list)
        self.clean_all_button.pack(side="right", padx=(0, 6))
        self.clean_selected_button = ttk.Button(
            file_actions, text="Clean Selected", command=self._clean_selected_list)
        self.clean_selected_button.pack(side="right", padx=(0, 6))
        columns = ("number", "file", "status", "details")
        # Keep the requested minimum compact enough that Current Activity,
        # Worker Status, and the session log all remain visible at 1080p.
        # This table still receives all spare vertical room through expand=True.
        self.file_tree = ttk.Treeview(files_frame, columns=columns, show="headings", height=8)
        self.file_tree.heading("number", text="#")
        self.file_tree.heading("file", text="File")
        self.file_tree.heading("status", text="Status")
        self.file_tree.heading("details", text="Changes / Result")
        self.file_tree.column("number", width=55, anchor="e", stretch=False)
        self.file_tree.column("file", width=360)
        self.file_tree.column("status", width=135, stretch=False)
        self.file_tree.column("details", width=430)
        scroll = ttk.Scrollbar(files_frame, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=scroll.set)
        self.file_tree.bind("<Button-3>", self._show_file_menu)
        self.file_tree.bind("<Shift-F10>", self._show_selected_file_menu)
        self.file_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        activity = self._section(self, "Current Activity")
        activity.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Label(activity, textvariable=self.activity_file, style="Activity.TLabel").pack(
            anchor="w",
        )
        ttk.Label(activity, textvariable=self.activity_action, style="Muted.TLabel").pack(
            anchor="w", pady=(2, 7),
        )
        progress_holder = ttk.Frame(activity)
        progress_holder.pack(fill="x")
        self.progress = ttk.Progressbar(
            progress_holder, maximum=100, style="Main.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x")
        ttk.Label(
            progress_holder, textvariable=self.progress_text,
            style="ProgressText.TLabel",
        ).place(relx=.5, rely=.5, anchor="center")
        info = ttk.Frame(activity)
        info.pack(fill="x", pady=(6, 0))
        ttk.Label(info, textvariable=self.counter_text).pack(side="left")
        ttk.Label(info, textvariable=self.elapsed_text).pack(side="right")
        controls = ttk.Frame(activity)
        controls.pack(fill="x", pady=(8, 0))
        self.pause_button = ttk.Button(controls, text="Pause", command=self._toggle_pause, state="disabled")
        self.pause_button.pack(side="left")
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)

        lower = ttk.Frame(self)
        lower.pack(fill="x", padx=12, pady=(0, 8))
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)

        worker_frame = self._section(lower, "Worker Status")
        worker_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.worker_tree = ttk.Treeview(
            worker_frame, columns=("worker", "action", "file"), show="headings", height=3,
        )
        self.worker_tree.heading("worker", text="Worker")
        self.worker_tree.heading("action", text="Current action")
        self.worker_tree.heading("file", text="Current file")
        self.worker_tree.column("worker", width=65, stretch=False, anchor="center")
        self.worker_tree.column("action", width=150, stretch=False)
        self.worker_tree.column("file", width=340)
        self.worker_tree.pack(fill="both", expand=True)

        summary = self._section(lower, "Run Summary")
        summary.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        for row, key in enumerate(("Found", "Inspected", "Cleaned", "Skipped", "Failed")):
            ttk.Label(summary, text=key).grid(row=row, column=0, sticky="w", padx=8, pady=3)
            ttk.Label(summary, textvariable=self.summary_vars[key], style="Summary.TLabel").grid(
                row=row, column=1, sticky="e", padx=8, pady=3,
            )
        summary.columnconfigure(0, weight=1)

        log_frame = self._section(self, "Activity Log")
        log_frame.pack(fill="x", padx=12, pady=(0, 8))
        self.activity_log = tk.Text(
            log_frame, height=7, wrap="word", state="disabled")
        self.activity_log.pack(fill="x")
        ttk.Label(
            self, text=f"MKVCleaner {__version__}", style="Footer.TLabel",
        ).pack(side="bottom", anchor="e", padx=14, pady=(0, 8))

    def apply_theme(self, mode: str) -> None:
        colors = COLORS[mode]
        self.configure(bg=colors["bg"])
        style = ttk.Style(self)
        style.theme_use("clam")
        base = {"background": colors["bg"], "foreground": colors["text"]}
        style.configure(".", font=("Segoe UI", 10), **base)
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabelframe", background=colors["panel"], bordercolor=colors["border"])
        style.configure("TLabelframe.Label", background=colors["panel"], foreground=colors["text"],
                        font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
        style.configure("Activity.TLabel", background=colors["panel"], foreground=colors["text"],
                        font=("Segoe UI", 12, "bold"))
        style.configure("AboutTitle.TLabel", background=colors["bg"], foreground=colors["text"],
                        font=("Segoe UI", 18, "bold"))
        style.configure("Muted.TLabel", background=colors["panel"], foreground=colors["muted"])
        style.configure("Warning.TLabel", background=colors["panel"], foreground="#e6b450")
        style.configure("ToolMissing.TLabel", background="#7f1d1d", foreground="#ffffff",
                        font=("Segoe UI", 10, "bold"))
        style.configure("ToolReady.TLabel", background="#166534", foreground="#ffffff",
                        font=("Segoe UI", 10, "bold"))
        style.configure("Footer.TLabel", background=colors["bg"], foreground=colors["muted"],
                        font=("Segoe UI", 9))
        style.configure("Summary.TLabel", background=colors["panel"], foreground=colors["text"],
                        font=("Segoe UI", 10, "bold"))
        style.configure("TButton", background=colors["field"], foreground=colors["text"],
                        bordercolor=colors["border"], padding=(10, 6))
        style.map("TButton", background=[("active", colors["border"])])
        style.configure("Accent.TButton", background=colors["accent"], foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", colors["accent"])])
        style.configure("TEntry", fieldbackground=colors["field"], foreground=colors["text"],
                        insertcolor=colors["text"], bordercolor=colors["border"])
        style.configure("TCombobox", fieldbackground=colors["field"], foreground=colors["text"],
                        arrowcolor=colors["text"])
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", colors["field"]),
                ("disabled", colors["panel"]),
            ],
            foreground=[
                ("readonly", colors["text"]),
                ("disabled", colors["muted"]),
            ],
            selectbackground=[
                ("readonly", colors["field"]),
            ],
            selectforeground=[
                ("readonly", colors["text"]),
            ],
        )
        self.option_add("*TCombobox*Listbox.background", colors["field"])
        self.option_add("*TCombobox*Listbox.foreground", colors["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", colors["select"])
        self.option_add("*TCombobox*Listbox.selectForeground", colors["text"])
        style.configure("Treeview", background=colors["panel"], fieldbackground=colors["panel"],
                        foreground=colors["text"], rowheight=25, bordercolor=colors["border"])
        style.configure("Treeview.Heading", background=colors["field"], foreground=colors["text"],
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", colors["select"])])
        style.configure("Main.Horizontal.TProgressbar", background=colors["accent"],
                        troughcolor=colors["field"], bordercolor=colors["field"])
        style.configure("ProgressText.TLabel", background=colors["field"],
                        foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.configure("TNotebook", background=colors["bg"], bordercolor=colors["border"])
        style.configure("TNotebook.Tab", background=colors["field"], foreground=colors["text"],
                        padding=(12, 7))
        style.map("TNotebook.Tab", background=[("selected", colors["panel"])])
        for status, color in STATUS_COLORS.items():
            tag = status.lower().replace(" ", "_").replace("(", "").replace(")", "")
            self.file_tree.tag_configure(tag, foreground=color)
        if hasattr(self, "activity_log"):
            self.activity_log.configure(
                background=colors["field"], foreground=colors["text"],
                insertbackground=colors["text"], selectbackground=colors["select"])

    def _append_activity_log(self, text: str) -> None:
        """Append one copyable entry to the Cleaner tab's session log."""
        log = getattr(self, "activity_log", None)
        if log is None:
            return
        log.configure(state="normal")
        log.insert("end", text.rstrip() + "\n")
        log.see("end")
        log.configure(state="disabled")

    def _browse(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Choose input folder")
        if selected:
            self.input_var.set(selected)

    def _settings(self) -> None:
        if self.running:
            messagebox.showinfo("Run in Progress", "Settings can be changed after this run finishes.")
            return
        try:
            self.config = load_config(self.config_path)
        except ValueError as exc:
            messagebox.showerror("Invalid config.json", str(exc))
            return
        dialog = SettingsDialog(self, self.config)
        self.wait_window(dialog)
        try:
            self.config = load_config(self.config_path)
            self._sync_track_name_options()
        except ValueError:
            pass
        self.refresh_tool_availability()

    def _sync_track_name_options(self) -> None:
        self.remove_video_names_var.set(self.config.remove_video_track_names)
        self.remove_audio_names_var.set(self.config.remove_audio_track_names)
        self.remove_subtitle_names_var.set(self.config.remove_subtitle_track_names)
        self.disable_default_subtitles_var.set(
            self.config.disable_default_subtitles_for_english_audio)
        self.clear_subtitle_default_var.set(
            self.config.clear_all_subtitle_default_flags)
        self.clear_subtitle_forced_var.set(
            self.config.clear_all_subtitle_forced_flags)

    def _set_track_name_option(self, track_type: str) -> None:
        if self.running:
            self._sync_track_name_options()

    def _set_default_subtitle_option(self) -> None:
        if self.running:
            self.disable_default_subtitles_var.set(
                self.config.disable_default_subtitles_for_english_audio)

    def _set_subtitle_flag_option(self) -> None:
        if self.running:
            self.clear_subtitle_default_var.set(
                self.config.clear_all_subtitle_default_flags)
            self.clear_subtitle_forced_var.set(
                self.config.clear_all_subtitle_forced_flags)

    def _config_with_run_options(self, config: CleanerConfig) -> CleanerConfig:
        """Apply main-page track-name choices without changing saved defaults."""
        return replace(
            config,
            remove_video_track_names=self.remove_video_names_var.get(),
            remove_audio_track_names=self.remove_audio_names_var.get(),
            remove_subtitle_track_names=self.remove_subtitle_names_var.get(),
            disable_default_subtitles_for_english_audio=(
                self.disable_default_subtitles_var.get()),
            clear_all_subtitle_default_flags=(
                self.clear_subtitle_default_var.get()),
            clear_all_subtitle_forced_flags=(
                self.clear_subtitle_forced_var.get()),
        )

    def refresh_tool_availability(self) -> bool:
        """Refresh MKVToolNix status without affecting Encoder availability."""
        try:
            config = load_config(self.config_path)
            mkvmerge = locate_mkvmerge(config.mkvmerge_path)
            locate_mkvpropedit(mkvmerge)
            available = True
        except Exception:
            available = False
        self.tools_available = available
        if available:
            self.tool_availability_text.set(
                "MKVToolNix detected — Cleaner features are available.")
            self.tool_availability_label.configure(style="ToolReady.TLabel")
        else:
            self.tool_availability_text.set(
                "MKVToolNix not found — Cleaner features are disabled. "
                "Encoding remains available. Open Settings for detection and download help.")
            self.tool_availability_label.configure(style="ToolMissing.TLabel")
        if not self.running:
            self.start_button.configure(state="normal" if available else "disabled")
            self.clean_selected_button.configure(
                state="normal" if available else "disabled")
            self.clean_all_button.configure(
                state="normal" if available else "disabled")
        if self.tool_availability_callback:
            self.tool_availability_callback(available)
        return available

    def sync_external_queue(self, queued: dict[Path, tuple[str, str]]) -> None:
        """Show Encoder cleaning requests immediately, before Cleaner claims them."""
        self.external_queue = {
            path.resolve(): display for path, display in queued.items()}
        self._render_pending_queue()

    def _render_pending_queue(self) -> None:
        normalized = dict(self.external_queue)
        normalized.update(self.dropped_queue)
        for path, row in list(self.queued_row_ids.items()):
            if path not in normalized:
                if self.file_tree.exists(row):
                    self.file_tree.delete(row)
                self.queued_row_ids.pop(path, None)
        for path, (status, detail) in normalized.items():
            if path in self.row_ids:
                continue
            row = self.queued_row_ids.get(path)
            tag = status.lower().replace(" ", "_")
            values = ("Q", path.name, status, detail)
            if row and self.file_tree.exists(row):
                self.file_tree.item(row, values=values, tags=(tag,))
            else:
                self.queued_row_ids[path] = self.file_tree.insert(
                    "", "end", values=values, tags=(tag,))

    def add_dropped_paths(self, dropped: list[str | Path]) -> int:
        """Queue Explorer-dropped MKV files or folders for the next run."""
        entries = expand_dropped_paths(dropped, {".mkv"})
        added = 0
        for path, _source_root in entries:
            if (path in self.dropped_queue or path in self.external_queue
                    or path in self.row_ids):
                continue
            self.dropped_queue[path] = (
                "Queued", "Dropped — press Start to clean")
            added += 1
        self._render_pending_queue()
        return added

    def _path_for_row(self, row: str) -> Path | None:
        for paths in (self.row_ids, self.queued_row_ids):
            for path, item in paths.items():
                if item == row:
                    return path
        return None

    def _selected_paths(self, clicked_row: str | None = None) -> list[Path]:
        rows = list(self.file_tree.selection())
        if clicked_row and clicked_row not in rows:
            rows = [clicked_row]
        paths = [self._path_for_row(row) for row in rows]
        return [path for path in paths if path is not None]

    def _clean_selected_list(self) -> None:
        paths = [path for path in self._selected_paths()
                 if path.suffix.lower() == ".mkv" and path.is_file()]
        if not paths:
            messagebox.showinfo(
                "Clean Selected",
                "Select one or more available MKV files first. Files still "
                "waiting for an encode output will remain queued.", parent=self)
            return
        self.start_paths(paths)

    def _clean_all_list(self) -> None:
        paths = []
        seen: set[Path] = set()
        for path in (*self.row_ids, *self.queued_row_ids):
            resolved = path.resolve()
            if (resolved not in seen and resolved.suffix.lower() == ".mkv"
                    and resolved.is_file()):
                seen.add(resolved)
                paths.append(resolved)
        if not paths:
            messagebox.showinfo(
                "Clean All",
                "There are no currently available MKV files in the Cleaner "
                "list. Files waiting for an encode output will remain queued.",
                parent=self)
            return
        waiting = sum(1 for path in self.queued_row_ids if not path.is_file())
        waiting_text = (f"\n\n{waiting} file{'s are' if waiting != 1 else ' is'} "
                        "still waiting for an encode and will remain queued."
                        if waiting else "")
        if messagebox.askyesno(
                "Clean All",
                f"Clean or re-clean all {len(paths)} currently available MKV "
                f"file{'s' if len(paths) != 1 else ''} in this list?"
                f"{waiting_text}", parent=self):
            self.start_paths(paths)

    def _show_selected_file_menu(self, event=None):
        selected = self.file_tree.selection()
        if selected:
            bounds = self.file_tree.bbox(selected[0])
            x = self.file_tree.winfo_rootx() + (bounds[0] if bounds else 0) + 20
            y = self.file_tree.winfo_rooty() + (bounds[1] if bounds else 0) + 20
            self._open_file_menu(selected[0], x, y)
        return "break"

    def _show_file_menu(self, event) -> None:
        row = self.file_tree.identify_row(event.y)
        if not row:
            return
        if row not in self.file_tree.selection():
            self.file_tree.selection_set(row)
        self.file_tree.focus(row)
        self._open_file_menu(row, event.x_root, event.y_root)

    def _open_file_menu(self, row: str, x: int, y: int) -> None:
        path = self._path_for_row(row)
        if path is None:
            return
        selected = self._selected_paths(row)
        values = list(self.file_tree.item(row, "values"))
        status = str(values[2]) if len(values) > 2 else ""
        idle_file = not self.running and path.is_file()
        menu = tk.Menu(self, tearoff=False)
        action = "Clean Again" if status in {"Done", "Skipped", "Failed"} else (
            "Retry Inspection" if status == "Failed" else "Clean This File")
        menu.add_command(
            label=action, command=lambda: self._clean_again(path),
            state="normal" if idle_file else "disabled")
        menu.add_command(
            label="Clean Selected Files",
            command=lambda: self.start_paths(selected),
            state="normal" if not self.running and selected
            and all(item.is_file() for item in selected) else "disabled")
        menu.add_command(
            label="View Cleanup Result",
            command=lambda: messagebox.showinfo(
                "Cleanup result", f"{path.name}\n\nStatus: {status}\n\n"
                f"{values[3] if len(values) > 3 else '—'}", parent=self))
        menu.add_separator()
        movable = not self.running
        selected_rows = list(self.file_tree.selection()) or [row]
        for label, destination in (
                ("Move to Top", "top"), ("Move Up", "up"),
                ("Move Down", "down"), ("Move to Bottom", "bottom")):
            menu.add_command(
                label=label,
                command=lambda where=destination, rows=selected_rows:
                    self._move_file_rows(rows, where),
                state="normal" if movable else "disabled")
        menu.add_separator()
        play_path = _cleaner_play_path(path, status)
        menu.add_command(
            label="Play Video",
            command=lambda target=play_path: self._play_video(target),
            state="normal" if play_path is not None else "disabled")
        menu.add_command(
            label=("Send File to Rename Tab" if len(selected) == 1
                   else f"Send {len(selected)} Files to Rename Tab"),
            command=lambda paths=selected: self.rename_request_callback(paths),
            state="normal" if self.rename_request_callback else "disabled")
        menu.add_command(
            label="Add This Directory to Rename Tab...",
            command=lambda folder=path.parent: self.rename_directory_callback(folder),
            state="normal" if self.rename_directory_callback else "disabled")
        menu.add_command(
            label="Rename File...", command=lambda: self._rename_one(path),
            state="normal" if idle_file else "disabled")
        menu.add_command(
            label="Rename Selected Files...",
            command=lambda: self._rename_selected(selected),
            state="normal" if not self.running and len(selected) > 1
            and all(item.is_file() for item in selected) else "disabled")
        menu.add_command(label="Open File Location",
                         command=lambda: self._open_file_location(path))
        menu.add_command(label="Copy Filename",
                         command=lambda: self._copy_text(path.name))
        menu.add_command(label="Copy Full Path",
                         command=lambda: self._copy_text(str(path)))
        menu.add_command(
            label="Remove From Queue" if status in {"Queued", "Waiting for Encode"}
            else "Remove From List",
            command=lambda: self._remove_paths(selected),
            state="normal" if not self.running else "disabled")
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _clean_again(self, path: Path) -> bool:
        """Reinspect one file with the Cleaner options visible right now.

        A completed/skipped row describes the options used by its previous run.
        Capture a new run configuration before resetting that row so a newly
        selected one-run option (for example Video track names) cannot be
        replaced by the saved defaults or the stale inspection result.
        """
        try:
            run_config = self._config_with_run_options(
                load_config(self.config_path))
        except ValueError as exc:
            messagebox.showerror("Invalid config.json", str(exc), parent=self)
            return False
        return self.start_paths([path], run_config=run_config)

    def _move_file_row(self, row: str, destination: str) -> None:
        self._move_file_rows([row], destination)

    def _move_file_rows(self, rows: list[str], destination: str) -> None:
        current = list(self.file_tree.get_children())
        order = _moved_row_order(current, rows, destination)
        for index, row in enumerate(order):
            self.file_tree.move(row, "", index)

    def _open_file_location(self, path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path.parent))  # type: ignore[attr-defined]
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

    def paths_renamed(self, mapping: dict[Path, Path]) -> None:
        """Migrate idle Cleaner rows and queued paths after a batch rename."""
        resolved = {Path(old): Path(new) for old, new in mapping.items()}
        self.external_queue = {
            resolved.get(path, path): value
            for path, value in self.external_queue.items()}
        self.dropped_queue = {
            resolved.get(path, path): value
            for path, value in self.dropped_queue.items()}
        records: list[tuple[Path, Path, str, dict[Path, str]]] = []
        for old, new in resolved.items():
            row = self.row_ids.get(old)
            row_group = self.row_ids
            if row is None:
                row = self.queued_row_ids.get(old)
                row_group = self.queued_row_ids
            if row is not None:
                records.append((old, new, row, row_group))
        for old, _new, _row, row_group in records:
            row_group.pop(old, None)
        for _old, new, row, row_group in records:
            if row is not None and self.file_tree.exists(row):
                values = list(self.file_tree.item(row, "values"))
                tags = self.file_tree.item(row, "tags")
                index = self.file_tree.index(row)
                selected = row in self.file_tree.selection()
                values[1] = new.name
                self.file_tree.delete(row)
                new_row = self.file_tree.insert("", index, values=values, tags=tags)
                row_group[new] = new_row
                if selected:
                    self.file_tree.selection_add(new_row)
        self._render_pending_queue()

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _remove_paths(self, paths: list[Path]) -> None:
        for path in paths:
            row = self.row_ids.pop(path, None) or self.queued_row_ids.pop(path, None)
            if row and self.file_tree.exists(row):
                self.file_tree.delete(row)
            self.dropped_queue.pop(path, None)
            self.external_queue.pop(path, None)
            if self.queue_remove_callback:
                self.queue_remove_callback(path)

    def _remove_all(self) -> None:
        """Clear every Cleaner row without deleting any media files."""
        if self.running:
            return
        paths = list(dict.fromkeys(
            (*self.row_ids, *self.queued_row_ids,
             *self.dropped_queue, *self.external_queue)))
        if not paths:
            return
        pending = bool(self.dropped_queue or self.external_queue)
        if not pending:
            for row in self.file_tree.get_children():
                values = self.file_tree.item(row, "values")
                status = str(values[2]) if len(values) > 2 else ""
                if status in {"Queued", "Waiting for Encode", "Waiting"}:
                    pending = True
                    break
        if pending and not messagebox.askyesno(
            "Remove All Cleaner Files",
            "Remove every file from the Cleaner list?\n\n"
            "Queued and waiting cleanup requests will be cancelled. "
            "No video files will be deleted or changed.",
            parent=self,
        ):
            return
        self._remove_paths(paths)
        self.activity_file.set("Cleaner list cleared")
        self.activity_action.set(
            "Removed all rows and pending cleanup requests; no files were deleted.")
        CleanerGUI._append_activity_log(
            self, f"Removed {len(paths)} file(s) from the Cleaner list.")

    def _rename_allowed(self, paths: list[Path]) -> bool:
        if self.running:
            messagebox.showinfo(
                "Cleaner is running", "Wait for Cleaner to finish before renaming files.",
                parent=self)
            return False
        if self.rename_guard_callback:
            allowed, reason = self.rename_guard_callback(paths)
            if not allowed:
                messagebox.showwarning("Rename unavailable", reason, parent=self)
                return False
        return True

    def _rename_one(self, path: Path) -> None:
        if not self._rename_allowed([path]):
            return
        new_name = simpledialog.askstring(
            "Rename MKV file", "New filename:", initialvalue=path.name, parent=self)
        if new_name is None or new_name == path.name:
            return
        try:
            plan = plan_filename_replacements([path], path.name, new_name)
            self._apply_rename_plan(plan)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot rename file", str(exc), parent=self)

    def _rename_selected(self, paths: list[Path]) -> None:
        if not self._rename_allowed(paths):
            return
        find_text = simpledialog.askstring(
            "Rename selected MKV files", "Text to find in selected filenames:",
            parent=self)
        if find_text is None:
            return
        replacement = simpledialog.askstring(
            "Rename selected MKV files",
            "Replacement text (leave blank to remove it):", parent=self)
        if replacement is None:
            return
        try:
            plan = plan_filename_replacements(paths, find_text, replacement)
        except ValueError as exc:
            messagebox.showerror("Cannot rename selected files", str(exc), parent=self)
            return
        preview_lines = [f"{source.name}  →  {destination.name}"
                         for source, destination in plan.items()]
        preview = "\n".join(preview_lines[:12])
        if len(preview_lines) > 12:
            preview += f"\n…and {len(preview_lines) - 12} more"
        if not messagebox.askyesno(
                "Confirm file renames",
                f"Rename {len(plan)} file(s)?\n\n{preview}", parent=self):
            return
        try:
            self._apply_rename_plan(plan)
        except OSError as exc:
            messagebox.showerror("Cannot rename selected files", str(exc), parent=self)

    def _apply_rename_plan(self, plan: dict[Path, Path]) -> None:
        completed: list[tuple[Path, Path]] = []
        try:
            for source, destination in plan.items():
                source.rename(destination)
                completed.append((source, destination))
        except OSError:
            for source, destination in reversed(completed):
                try:
                    destination.rename(source)
                except OSError:
                    pass
            raise
        for source, destination in completed:
            row = self.row_ids.pop(source, None)
            queued = False
            if row is None:
                row = self.queued_row_ids.pop(source, None)
                queued = True
            if row:
                values = list(self.file_tree.item(row, "values"))
                values[1] = destination.name
                self.file_tree.item(row, values=values)
                (self.queued_row_ids if queued else self.row_ids)[destination] = row
            if source in self.dropped_queue:
                self.dropped_queue[destination] = self.dropped_queue.pop(source)
            if source in self.external_queue:
                self.external_queue[destination] = self.external_queue.pop(source)
        self.activity_action.set(f"Renamed {len(completed)} file(s).")
        for source, destination in completed:
            CleanerGUI._append_activity_log(
                self, f"Renamed: {source.name} → {destination.name}")

    def _reset_run(self) -> None:
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        for item in self.worker_tree.get_children():
            self.worker_tree.delete(item)
        self.row_ids.clear()
        self.queued_row_ids.clear()
        self.workers.clear()
        self.inspected = self.completed = 0
        self.counts = {"Cleaned": 0, "Skipped": 0, "Failed": 0, "Dry-run": 0, "Cancelled": 0}
        self.progress["value"] = 0
        self.progress_text.set("0.0%")
        for value in self.summary_vars.values():
            value.set("0")

    def _start(self) -> None:
        if self.running:
            return
        if not self.refresh_tool_availability():
            return
        if self.can_start_callback and not self.can_start_callback():
            messagebox.showinfo(
                "Encoder is running",
                "Wait for the Encoder session to finish before starting Cleaner.",
                parent=self)
            return
        if self.dropped_queue:
            paths = list(self.dropped_queue)
            saved = dict(self.dropped_queue)
            self.dropped_queue.clear()
            self._render_pending_queue()
            if not self.start_paths(paths):
                self.dropped_queue.update(saved)
                self._render_pending_queue()
            return
        try:
            config = self._config_with_run_options(load_config(self.config_path))
            input_path = Path(self.input_var.get().strip()).expanduser()
            if not self.input_var.get().strip():
                raise ValueError("Choose an input folder first.")
            mkvmerge = locate_mkvmerge(config.mkvmerge_path)
        except Exception as exc:
            messagebox.showerror("Cannot Start", str(exc), parent=self)
            return

        self._begin_run(config, mkvmerge)
        threading.Thread(
            target=self._discover_and_run,
            args=(input_path, mkvmerge, config, self.current_logger),
            name="mkvcleaner-run", daemon=True,
        ).start()

    def _begin_run(self, config: CleanerConfig, mkvmerge: Path) -> None:
        self.config = config
        self._reset_run()
        self.total = 0
        for worker_id in range(1, config.threads + 1):
            row = self.worker_tree.insert("", "end", values=(worker_id, "Idle", "—"))
            self.workers[worker_id] = (row, "")

        log_dir = Path(config.log_directory)
        if not log_dir.is_absolute():
            log_dir = application_directory() / log_dir
        logger = RunLogger(log_dir, config.json_logging)
        self.current_logger = logger
        self.running = True
        self.discovery_active = True
        self.paused = False
        self.run_event.set()
        self.stop_event.clear()
        self.started_at = time.monotonic()
        self.activity_file.set("Discovering MKV files")
        self.activity_action.set("Scanning the selected folder…")
        self.counter_text.set("Discovering • 0 found")
        self.progress_text.set("Scanning…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        CleanerGUI._append_activity_log(self, "Cleaner run started.")
        self.start_button.configure(state="disabled")
        self.settings_button.configure(state="disabled")
        self.remove_all_button.configure(state="disabled")
        self.clean_selected_button.configure(state="disabled")
        self.clean_all_button.configure(state="disabled")
        for check in self.run_option_checks:
            check.configure(state="disabled")
        self.pause_button.configure(state="normal", text="Pause")
        self.cancel_button.configure(state="normal")
 
    def start_paths(
        self,
        paths: list[Path],
        run_config: CleanerConfig | None = None,
    ) -> bool:
        """Start one Cleaner run for explicit files received from Encoder."""
        if self.running:
            return False
        if not self.refresh_tool_availability():
            return False
        unique = []
        seen: set[Path] = set()
        for path in paths:
            resolved = path.expanduser().resolve()
            if resolved.suffix.lower() == ".mkv" and resolved.is_file() and resolved not in seen:
                seen.add(resolved)
                unique.append(resolved)
        if not unique:
            return False
        try:
            config = run_config or self._config_with_run_options(
                load_config(self.config_path))
            mkvmerge = locate_mkvmerge(config.mkvmerge_path)
        except Exception as exc:
            messagebox.showerror("Cannot Start Cleaner", str(exc), parent=self)
            return False
        if self.start_guard_callback:
            allowed, reason = self.start_guard_callback(unique)
            if not allowed:
                messagebox.showwarning(
                    "File is busy",
                    reason or "One or more files are currently being encoded.",
                    parent=self)
                return False
        self._begin_run(config, mkvmerge)
        files = [MediaFile(path) for path in unique]
        self.events.put(("discovered", files))
        self.events.put(("discovery_done", files))
        threading.Thread(
            target=self._run,
            args=(files, mkvmerge, config, self.current_logger),
            name="mkvcleaner-selected", daemon=True,
        ).start()
        return True

    def _discover_and_run(self, input_path, mkvmerge, config, logger) -> None:
        files = []
        batch = []
        try:
            for media in scan_iter(
                input_path, config.recursive, self.run_event, self.stop_event,
            ):
                files.append(media)
                batch.append(media)
                if len(batch) >= 75:
                    self.events.put(("discovered", batch))
                    batch = []
            if batch:
                self.events.put(("discovered", batch))
            self.events.put(("discovery_done", files))
            if self.stop_event.is_set():
                return
            if not files:
                raise ValueError("No eligible MKV files were found.")
            self._run(files, mkvmerge, config, logger)
        except Exception as exc:
            self.events.put(("fatal", str(exc)))

    def _run(self, files, mkvmerge, config, logger) -> None:
        def emit(*event) -> None:
            self.events.put(event)

        def inspected(media) -> None:
            logger.add(media) if media.status == "Failed" or not media.clean_reasons else None
            emit("inspect", media)

        def inspect_started(media) -> None:
            emit("inspect_started", media)

        def inspect_waiting(media) -> None:
            emit("inspection_paused", media)

        def waiting(media, worker_id) -> None:
            emit("worker", media, worker_id, "Waiting (paused)")

        def started(media, worker_id) -> None:
            emit("worker", media, worker_id, "Dry run" if config.dry_run else "Starting")

        def phase(media, worker_id, action) -> None:
            emit("worker", media, worker_id, action)

        def result(media, worker_id) -> None:
            logger.add(media)
            emit("result", media, worker_id)

        try:
            results = process_files(
                files, mkvmerge, config,
                inspect_callback=inspected,
                inspect_start_callback=inspect_started,
                inspect_wait_callback=inspect_waiting,
                wait_callback=waiting,
                start_callback=started,
                phase_callback=phase,
                result_callback=result,
                run_event=self.run_event,
                stop_event=self.stop_event,
            )
            logger.finalize()
            emit("finished", results, logger)
        except Exception as exc:
            emit("fatal", str(exc))

    def _set_file(self, path: Path, status: str, details: str) -> None:
        row = self.row_ids[path]
        values = list(self.file_tree.item(row, "values"))
        values[2] = status
        values[3] = details or "—"
        tag = status.lower().replace(" ", "_").replace("(", "").replace(")", "")
        self.file_tree.item(row, values=values, tags=(tag,))
        self.file_tree.see(row)

    def _set_worker(self, worker_id: int, action: str, filename: str = "") -> None:
        row, _ = self.workers[worker_id]
        self.workers[worker_id] = (row, filename)
        self.worker_tree.item(row, values=(worker_id, action, filename or "—"))

    def _complete_one(self) -> None:
        self.completed += 1
        percent = self.completed * 100 / self.total if self.total else 0
        self.progress["value"] = percent
        self.progress_text.set(f"{percent:.1f}%")
        self.counter_text.set(f"{self.completed}/{self.total} files")

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "discovered":
            for media in event[1]:
                self.total += 1
                display = media.path.name
                row = self.file_tree.insert(
                    "", "end",
                    values=(self.total, display, "Waiting", "—"),
                    tags=("waiting",),
                )
                self.row_ids[media.path] = row
            self.summary_vars["Found"].set(str(self.total))
            self.counter_text.set(f"Discovering • {self.total} found")
            self.activity_action.set(f"Scanning… {self.total} eligible MKV file(s) found.")
        elif kind == "discovery_done":
            self.discovery_active = False
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=100)
            self.progress["value"] = 0
            self.progress_text.set("0.0%")
            if self.stop_event.is_set():
                for path in tuple(self.row_ids):
                    self._set_file(path, "Cancelled", "Cancelled before inspection")
                self.completed = self.total
                self.counts["Cancelled"] = self.total
                self.counter_text.set(f"{self.completed}/{self.total} files")
                self.activity_file.set("Run cancelled")
                self.activity_action.set(
                    f"Discovery stopped • {self.total} eligible MKV file(s) found."
                )
                self._finish_controls()
            elif self.total:
                CleanerGUI._append_activity_log(
                    self, f"Discovery complete: {self.total} MKV file(s) found.")
                self.counter_text.set(f"0/{self.total} files")
                self.activity_file.set("Discovery complete")
                self.activity_action.set(
                    f"Found {self.total} MKV file(s); starting inspection."
                )
        elif kind == "inspect_started":
            media = event[1]
            self._set_file(media.path, "Inspecting", "Reading MKV metadata")
            self.activity_file.set(media.path.name)
            self.activity_action.set("Inspecting metadata.")
        elif kind == "inspection_paused":
            self.activity_file.set(event[1].path.name)
            self.activity_action.set("Paused before the next inspection.")
        elif kind == "inspect":
            media = event[1]
            self.activity_file.set(media.path.name)
            if media.status == "Cancelled":
                self._set_file(media.path, "Cancelled", media.error)
                self.counts["Cancelled"] += 1
                self._complete_one()
            else:
                self.inspected += 1
                self.summary_vars["Inspected"].set(str(self.inspected))
            if media.status == "Failed":
                self._set_file(media.path, "Failed", media.error)
                CleanerGUI._append_activity_log(
                    self, f"Inspection failed: {media.path.name} — {media.error}")
                self.counts["Failed"] += 1
                self.summary_vars["Failed"].set(str(self.counts["Failed"]))
                self._complete_one()
            elif not media.clean_reasons:
                detail = "Already clean — no configured cleanup needed"
                if media.preflight_changes:
                    detail = " • ".join(media.preflight_changes) + " — " + detail
                self._set_file(media.path, "Skipped", detail)
                CleanerGUI._append_activity_log(
                    self, f"Skipped: {media.path.name} — already clean")
                self.counts["Skipped"] += 1
                self.summary_vars["Skipped"].set(str(self.counts["Skipped"]))
                self._complete_one()
            else:
                in_place = can_use_mkvpropedit(media, self.config)
                status = "Will clean" if in_place else "Will remux"
                method = "fast in-place edit" if in_place else "full remux required"
                self._set_file(
                    media.path, status,
                    ((" • ".join(media.preflight_changes) + " — ")
                     if media.preflight_changes else "")
                    + f"{' • '.join(media.clean_reasons)} — {method}",
                )
                CleanerGUI._append_activity_log(
                    self, f"Ready: {media.path.name} — {method}")
                self.activity_action.set(
                    "Inspection complete; queued for "
                    + ("fast in-place editing." if in_place else "full remuxing.")
                )
        elif kind == "worker":
            media, worker_id, action = event[1:]
            status = "Cleaning" if action == "Starting" else action
            self._set_worker(worker_id, action, media.path.name)
            self._set_file(media.path, status, " • ".join(media.clean_reasons))
            self.activity_file.set(media.path.name)
            self.activity_action.set(f"Worker {worker_id}: {action}")
        elif kind == "result":
            media, worker_id = event[1:]
            self._set_worker(worker_id, "Idle")
            if media.status == "Cleaned":
                detail = (
                    "Metadata cleaned in place with mkvpropedit"
                    if media.cleaning_method == "mkvpropedit" else "Cleaned with mkvmerge"
                )
                if media.preflight_changes:
                    detail = " • ".join(media.preflight_changes) + " — " + detail
                if media.verified:
                    detail += " and verified"
                self._set_file(media.path, "Done", detail)
                CleanerGUI._append_activity_log(
                    self, f"Cleaned: {media.path.name} — {detail}")
                self.counts["Cleaned"] += 1
                self.summary_vars["Cleaned"].set(str(self.counts["Cleaned"]))
            elif media.status == "Dry-run":
                self._set_file(media.path, "Dry run complete", "No files were changed")
                CleanerGUI._append_activity_log(
                    self, f"Dry run: {media.path.name} — no files changed")
                self.counts["Dry-run"] += 1
            elif media.status == "Cancelled":
                self._set_file(media.path, "Cancelled", media.error)
                CleanerGUI._append_activity_log(
                    self, f"Cancelled: {media.path.name} — {media.error}")
                self.counts["Cancelled"] += 1
            elif media.status == "Failed":
                self._set_file(media.path, "Failed", media.error)
                CleanerGUI._append_activity_log(
                    self, f"Failed: {media.path.name} — {media.error}")
                self.counts["Failed"] += 1
                self.summary_vars["Failed"].set(str(self.counts["Failed"]))
            self._complete_one()
        elif kind == "finished":
            results, logger = event[1:]
            saved = sum(item.bytes_saved for item in results if item.status == "Cleaned")
            self.activity_file.set("Run complete" if not self.stop_event.is_set() else "Run cancelled")
            self.activity_action.set(
                f"Cleaned {self.counts['Cleaned']}, skipped {self.counts['Skipped']}, "
                f"failed {self.counts['Failed']} • size saved {_format_bytes(saved)}"
            )
            CleanerGUI._append_activity_log(
                self, f"Run complete: cleaned {self.counts['Cleaned']}, "
                f"skipped {self.counts['Skipped']}, failed {self.counts['Failed']}.")
            self._finish_controls()
            if self.finished_callback:
                self.finished_callback(results)
        elif kind == "fatal":
            self.activity_file.set("Run failed")
            self.activity_action.set(event[1])
            CleanerGUI._append_activity_log(self, f"Run failed: {event[1]}")
            self._finish_controls()
            if self.finished_callback:
                self.finished_callback([])
            messagebox.showerror("MKVCleaner Error", event[1], parent=self)

    def _drain_events(self) -> None:
        try:
            for _ in range(12):
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _tick(self) -> None:
        if self.running:
            self.elapsed_text.set(f"Elapsed {_format_seconds(time.monotonic() - self.started_at)}")
        self.after(500, self._tick)

    def _toggle_pause(self) -> None:
        if not self.running:
            return
        if self.paused:
            self.paused = False
            self.run_event.set()
            self.pause_button.configure(text="Pause")
            if self.discovery_active:
                self.progress.start(12)
            self.activity_action.set("Resuming queued work.")
        else:
            self.paused = True
            self.run_event.clear()
            self.pause_button.configure(text="Resume")
            if self.discovery_active:
                self.progress.stop()
                self.activity_action.set("Pausing file discovery.")
            else:
                self.activity_action.set(
                    "Pausing before the next inspection or cleanup; "
                    "active operations will finish safely."
                )

    def _cancel(self) -> None:
        if not self.running:
            return
        if not messagebox.askyesno(
            "Cancel Run",
            "Stop queued files from starting?\n\nAn active MKVToolNix operation will finish safely.",
            parent=self,
        ):
            return
        self.stop_event.set()
        self.run_event.set()
        self.paused = False
        if self.discovery_active:
            self.progress.stop()
        self.pause_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.activity_action.set("Cancelling queued work; active operations are finishing.")

    def _finish_controls(self) -> None:
        if self.discovery_active:
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=100)
            self.progress["value"] = 0
            self.progress_text.set("0.0%")
            self.discovery_active = False
        self.running = False
        self.paused = False
        self.run_event.set()
        self.start_button.configure(
            state="normal" if self.tools_available else "disabled")
        self.settings_button.configure(state="normal")
        self.remove_all_button.configure(state="normal")
        self.clean_selected_button.configure(
            state="normal" if self.tools_available else "disabled")
        self.clean_all_button.configure(
            state="normal" if self.tools_available else "disabled")
        for check in self.run_option_checks:
            check.configure(state="normal")
        try:
            self.config = load_config(self.config_path)
            self._sync_track_name_options()
        except ValueError:
            pass
        self.pause_button.configure(state="disabled", text="Pause")
        self.cancel_button.configure(state="disabled")

    def _close(self) -> None:
        if self.running:
            messagebox.showinfo(
                "Run in Progress",
                "Cancel the run and wait for active operations to finish before closing.",
                parent=self,
            )
            return
        self.winfo_toplevel().destroy()


def main() -> int:
    os.environ["MKVCLEANER_GUI"] = "1"
    root = tk.Tk()
    root.title(f"MKVCleaner {__version__}")
    root.geometry("1120x780")
    root.minsize(900, 650)
    app = CleanerGUI(root)
    app.pack(fill="both", expand=True)
    root.protocol("WM_DELETE_WINDOW", app._close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
