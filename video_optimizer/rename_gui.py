from __future__ import annotations

import json
import os
import re
import stat
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import application_directory
from .scanner import VIDEO_EXTENSIONS


INVALID_WINDOWS_CHARS = set('<>:"/\\|?*')
RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass
class RenameConfig:
    default_input_directory: str = ""
    include_subfolders: bool = False


@dataclass
class RenameOptions:
    find: str = ""
    replacement: str = ""
    case_sensitive: bool = False
    prefix: str = ""
    suffix: str = ""
    remove_start: int = 0
    remove_end: int = 0
    case_mode: str = "Unchanged"
    preserve_resolution_case: bool = False
    normalize_episode: bool = False
    convert_x_format: bool = False
    spaces_to_dots: bool = False
    numbering: bool = False
    number_start: int = 1
    number_width: int = 2


def rename_config_path() -> Path:
    return application_directory() / "rename_config.json"


def load_rename_config(path: Path | None = None) -> RenameConfig:
    path = path or rename_config_path()
    if not path.exists():
        return RenameConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    return RenameConfig(
        default_input_directory=str(data.get("default_input_directory", "")),
        include_subfolders=bool(data.get("include_subfolders", False)),
    )


def save_rename_config(config: RenameConfig, path: Path | None = None) -> None:
    path = path or rename_config_path()
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")


def transform_stem(stem: str, options: RenameOptions, index: int = 0) -> str:
    """Apply Rename-tab operations in their documented order."""
    result = stem
    if options.remove_start:
        result = result[max(0, options.remove_start):]
    if options.remove_end:
        result = result[:-max(0, options.remove_end)] if len(result) > options.remove_end else ""
    if options.find:
        flags = 0 if options.case_sensitive else re.IGNORECASE
        result = re.sub(re.escape(options.find),
                        lambda _match: options.replacement, result, flags=flags)
    result = f"{options.prefix}{result}{options.suffix}"
    def convert_case(text: str) -> str:
        if options.case_mode == "lowercase":
            return text.lower()
        if options.case_mode == "UPPERCASE":
            return text.upper()
        if options.case_mode == "Title Case":
            return text.title()
        return text

    if options.preserve_resolution_case:
        parts = re.split(r"(?i)(720p|1080p|2160p)", result)
        result = "".join(part if index % 2 else convert_case(part)
                         for index, part in enumerate(parts))
    else:
        result = convert_case(result)
    if options.convert_x_format:
        result = re.sub(
            r"(?<![A-Za-z0-9])(\d{1,2})[xX](\d{1,3})(?!\d)",
            lambda match: f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}",
            result,
        )
    if options.normalize_episode:
        result = re.sub(
            r"(?i)(?<![A-Za-z0-9])s(\d{1,2})e(\d{1,3})(?:[-_. ]?e?(\d{1,3}))?",
            lambda match: (
                f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"
                + (f"-E{int(match.group(3)):02d}" if match.group(3) else "")
            ),
            result,
        )
    if options.numbering:
        number = options.number_start + index
        result = f"{number:0{max(1, options.number_width)}d} {result}"
    result = result.strip()
    if options.spaces_to_dots:
        result = result.replace(" ", ".")
    return result


def proposed_path(path: Path, options: RenameOptions, index: int = 0) -> Path:
    return path.with_name(transform_stem(path.stem, options, index) + path.suffix)


def validate_rename_plan(plan: dict[Path, Path]) -> None:
    """Validate the complete batch before any file is moved."""
    sources = {path.resolve() for path in plan}
    source_keys = {str(path).casefold() for path in sources}
    destinations: set[str] = set()
    for raw_source, raw_destination in plan.items():
        source = raw_source.resolve()
        destination = raw_destination.resolve()
        stem = destination.stem
        if not source.is_file():
            raise ValueError(f"Source file is missing: {source}")
        if (not stem or stem in {".", ".."}
                or destination.name.casefold() == source.suffix.casefold()):
            raise ValueError(f"The new filename is empty: {source.name}")
        if any(character in INVALID_WINDOWS_CHARS for character in destination.name):
            raise ValueError(f"The new filename contains an invalid character: {destination.name}")
        if destination.name.endswith((" ", ".")):
            raise ValueError(f"Windows filenames cannot end in a space or period: {destination.name}")
        if stem.split(".")[0].upper() in RESERVED_WINDOWS_NAMES:
            raise ValueError(f"The new filename is reserved by Windows: {destination.name}")
        key = str(destination).casefold()
        if key in destinations:
            raise ValueError(f"More than one file would become: {destination.name}")
        destinations.add(key)
        if (destination.exists() and key not in source_keys
                and destination != source):
            raise ValueError(f"A file already exists with this name: {destination.name}")


def _restore_writable_state(path: Path, was_writable: bool) -> None:
    """Undo a NAS/SMB rename that unexpectedly adds the read-only attribute."""
    if not was_writable:
        return
    mode = path.stat().st_mode
    if not mode & stat.S_IWRITE:
        os.chmod(path, mode | stat.S_IWRITE)
        if not path.stat().st_mode & stat.S_IWRITE:
            raise PermissionError(
                f"The renamed file became read-only and could not be corrected: {path}")


def execute_rename_plan(plan: dict[Path, Path]) -> dict[Path, Path]:
    """Rename a validated batch through temporary names, with rollback."""
    changed = {source.resolve(): destination.resolve()
               for source, destination in plan.items()
               if source.resolve() != destination.resolve()
               or source.name != destination.name}
    validate_rename_plan(changed)
    if not changed:
        return {}
    source_writable = {
        source: bool(source.stat().st_mode & stat.S_IWRITE)
        for source in changed
    }
    temporary: dict[Path, Path] = {}
    completed: dict[Path, Path] = {}
    try:
        for source in changed:
            # Do not begin the temporary name with a dot. Some Samba/NAS
            # configurations map dotfiles to the Windows Hidden attribute and
            # retain that attribute after the final rename.
            temp = source.with_name(f"mkvencoderq-rename-{uuid.uuid4().hex}{source.suffix}")
            source.rename(temp)
            temporary[source] = temp
        for source, destination in changed.items():
            temporary[source].rename(destination)
            completed[source] = destination
            _restore_writable_state(destination, source_writable[source])
        return changed
    except OSError:
        for source, destination in reversed(list(completed.items())):
            if destination.exists() and not source.exists():
                destination.rename(source)
                try:
                    _restore_writable_state(source, source_writable[source])
                except OSError:
                    pass
        for source, temp in reversed(list(temporary.items())):
            if temp.exists() and not source.exists():
                temp.rename(source)
        raise


class RenameGUI(ttk.Frame):
    """Independent batch-renaming workspace used by the combined application."""

    def __init__(self, master=None, busy_callback=None, renamed_callback=None,
                 companion_plan_callback=None) -> None:
        super().__init__(master)
        self.busy_callback = busy_callback or (lambda _path: (False, ""))
        self.renamed_callback = renamed_callback
        self.companion_plan_callback = companion_plan_callback
        try:
            self.config_data = load_rename_config()
        except (OSError, ValueError, json.JSONDecodeError):
            self.config_data = RenameConfig()
        self.paths: dict[str, Path] = {}
        self.last_rename: dict[Path, Path] = {}
        self.input_var = tk.StringVar(value=self.config_data.default_input_directory)
        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.case_sensitive_var = tk.BooleanVar()
        self.prefix_var = tk.StringVar()
        self.suffix_var = tk.StringVar()
        self.remove_start_var = tk.IntVar(value=0)
        self.remove_end_var = tk.IntVar(value=0)
        self.case_mode_var = tk.StringVar(value="Unchanged")
        self.preserve_resolution_case_var = tk.BooleanVar()
        self.normalize_episode_var = tk.BooleanVar()
        self.convert_x_var = tk.BooleanVar()
        self.spaces_to_dots_var = tk.BooleanVar()
        self.numbering_var = tk.BooleanVar()
        self.number_start_var = tk.IntVar(value=1)
        self.number_width_var = tk.IntVar(value=2)
        self.status_var = tk.StringVar(value="Add files or a folder to begin.")
        self._build()
        for variable in (
                self.find_var, self.replace_var, self.case_sensitive_var,
                self.prefix_var, self.suffix_var, self.remove_start_var,
                self.remove_end_var, self.case_mode_var,
                self.preserve_resolution_case_var, self.normalize_episode_var,
                self.convert_x_var, self.spaces_to_dots_var,
                self.numbering_var, self.number_start_var,
                self.number_width_var):
            variable.trace_add("write", lambda *_args: self.refresh_preview())

    def _build(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Input folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Browse...", command=self.browse_input).grid(row=0, column=2, padx=3)
        ttk.Button(top, text="Load Folder", command=self.load_input_folder).grid(row=0, column=3, padx=3)
        ttk.Button(top, text="Add Files...", command=self.add_files_dialog).grid(row=0, column=4, padx=3)
        ttk.Button(top, text="Add Folder...", command=self.add_folder_dialog).grid(row=0, column=5, padx=3)
        ttk.Button(top, text="Set Default Folder...", command=self.settings).grid(row=0, column=6, padx=3)
        top.columnconfigure(1, weight=1)

        controls = ttk.LabelFrame(self, text="Rename Controls", padding=8)
        controls.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(controls, text="Find").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.find_var, width=24).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(controls, text="Replace with").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.replace_var, width=24).grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Checkbutton(controls, text="Case Sensitive", variable=self.case_sensitive_var).grid(row=0, column=4, padx=6)
        ttk.Label(controls, text="Prefix").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(controls, textvariable=self.prefix_var).grid(row=1, column=1, sticky="ew", padx=4, pady=(6, 0))
        ttk.Label(controls, text="Suffix").grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(controls, textvariable=self.suffix_var).grid(row=1, column=3, sticky="ew", padx=4, pady=(6, 0))
        ttk.Label(controls, text="Remove from beginning").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(controls, from_=0, to=999, textvariable=self.remove_start_var, width=7).grid(row=2, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(controls, text="Remove from end").grid(row=2, column=2, sticky="w", pady=(6, 0))
        ttk.Spinbox(controls, from_=0, to=999, textvariable=self.remove_end_var, width=7).grid(row=2, column=3, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(controls, text="Case conversion").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(controls, textvariable=self.case_mode_var, state="readonly",
                     values=("Unchanged", "lowercase", "UPPERCASE", "Title Case"), width=18).grid(row=3, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Checkbutton(controls, text="Normalize S01E01", variable=self.normalize_episode_var).grid(row=3, column=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(controls, text="Convert 1x02 to S01E02", variable=self.convert_x_var).grid(row=3, column=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            controls, text="Keep 720p / 1080p / 2160p unchanged",
            variable=self.preserve_resolution_case_var,
        ).grid(row=3, column=4, sticky="w", padx=6, pady=(6, 0))
        ttk.Checkbutton(controls, text="Sequential numbering", variable=self.numbering_var).grid(row=4, column=0, sticky="w", pady=(6, 0))
        number_frame = ttk.Frame(controls)
        number_frame.grid(row=4, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(number_frame, text="Start").pack(side="left")
        ttk.Spinbox(number_frame, from_=0, to=99999, textvariable=self.number_start_var, width=6).pack(side="left", padx=4)
        ttk.Label(number_frame, text="Digits").pack(side="left", padx=(8, 0))
        ttk.Spinbox(number_frame, from_=1, to=8, textvariable=self.number_width_var, width=4).pack(side="left", padx=4)
        ttk.Checkbutton(
            controls, text="Spaces to dots", variable=self.spaces_to_dots_var
        ).grid(row=4, column=3, sticky="w", pady=(6, 0))
        ttk.Button(controls, text="Reset Rename Controls", command=self.reset_controls).grid(row=4, column=4, sticky="e", padx=6, pady=(6, 0))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)

        buttons = ttk.Frame(self, padding=(10, 0, 10, 5))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Check All", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(buttons, text="Uncheck All", command=lambda: self.set_all(False)).pack(side="left", padx=4)
        ttk.Button(buttons, text="Remove Checked", command=self.remove_checked).pack(side="left", padx=(12, 4))
        ttk.Button(buttons, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(buttons, text="Remove All", command=self.remove_all).pack(side="left", padx=4)
        self.undo_button = ttk.Button(buttons, text="Undo Last Rename", command=self.undo, state="disabled")
        self.undo_button.pack(side="right")
        ttk.Button(
            buttons, text="Rename Selected File",
            command=self.apply_selected_rename).pack(side="right", padx=6)
        ttk.Button(buttons, text="Rename Checked Files",
                   command=self.apply_renames).pack(side="right")

        columns = ("checked", "current", "proposed", "path", "status")
        self.table = ttk.Treeview(self, columns=columns, show="headings", height=17, selectmode="extended")
        for key, label, width in (
                ("checked", "Rename", 75), ("current", "Current Filename", 260),
                ("proposed", "Proposed Filename", 260), ("path", "Path", 360),
                ("status", "Status", 150)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=key in {"current", "proposed", "path"},
                              anchor="center" if key in {"checked", "status"} else "w")
        self.table.bind("<Button-1>", self._table_click)
        self.table.bind("<Button-3>", self._show_context_menu)
        self.context_menu = tk.Menu(self, tearoff=False)
        self.context_menu.add_command(
            label="Check Selected", command=lambda: self.set_selected(True))
        self.context_menu.add_command(
            label="Uncheck Selected", command=lambda: self.set_selected(False))
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Open File Location", command=self._open_context_location)
        self.context_menu.add_command(
            label="Copy Filename", command=self._copy_context_filename)
        self.context_menu.add_command(
            label="Copy Directory Name", command=self._copy_context_directory_name)
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Remove Selected from List", command=self.remove_selected)
        self.table.pack(fill="both", expand=True, padx=10)
        log_frame = ttk.LabelFrame(self, text="Activity Log", padding=8)
        log_frame.pack(fill="x", padx=10, pady=(8, 6))
        self.activity_log = tk.Text(
            log_frame, height=7, wrap="word", state="disabled")
        background = ttk.Style(self).lookup("Treeview", "fieldbackground") or "#202020"
        foreground = ttk.Style(self).lookup("Treeview", "foreground") or "#f0f0f0"
        selected = ttk.Style(self).lookup("Treeview", "selectbackground") or "#315a7d"
        self.activity_log.configure(
            background=background, foreground=foreground,
            insertbackground=foreground, selectbackground=selected)
        self.activity_log.pack(fill="x")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    def _append_activity_log(self, text: str) -> None:
        """Append one copyable entry to the Rename tab's session log."""
        log = getattr(self, "activity_log", None)
        if log is None:
            return
        log.configure(state="normal")
        log.insert("end", text.rstrip() + "\n")
        log.see("end")
        log.configure(state="disabled")

    def options(self) -> RenameOptions:
        def integer(variable: tk.IntVar, default: int) -> int:
            try:
                return int(variable.get())
            except (tk.TclError, ValueError):
                return default
        return RenameOptions(
            find=self.find_var.get(), replacement=self.replace_var.get(),
            case_sensitive=self.case_sensitive_var.get(), prefix=self.prefix_var.get(),
            suffix=self.suffix_var.get(), remove_start=max(0, integer(self.remove_start_var, 0)),
            remove_end=max(0, integer(self.remove_end_var, 0)),
            case_mode=self.case_mode_var.get(), normalize_episode=self.normalize_episode_var.get(),
            preserve_resolution_case=self.preserve_resolution_case_var.get(),
            convert_x_format=self.convert_x_var.get(),
            spaces_to_dots=self.spaces_to_dots_var.get(),
            numbering=self.numbering_var.get(),
            number_start=integer(self.number_start_var, 1),
            number_width=max(1, integer(self.number_width_var, 2)),
        )

    def _table_click(self, event):
        if self.table.identify_region(event.x, event.y) == "cell" and self.table.identify_column(event.x) == "#1":
            row = self.table.identify_row(event.y)
            if row:
                values = list(self.table.item(row, "values"))
                values[0] = "☐" if values[0] == "☑" else "☑"
                self.table.item(row, values=values)
                self.refresh_preview()
                return "break"
        return None

    def _show_context_menu(self, event):
        row = self.table.identify_row(event.y)
        if not row:
            return None
        if row not in self.table.selection():
            self.table.selection_set(row)
        self.table.focus(row)
        self.context_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _context_path(self) -> Path | None:
        row = self.table.focus()
        return self.paths.get(row)

    def _open_context_location(self) -> None:
        path = self._context_path()
        if path is None:
            return
        try:
            if os.name == "nt":
                os.startfile(str(path.parent))  # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError as exc:
            messagebox.showerror(
                "Open File Location", f"Could not open the folder:\n\n{exc}",
                parent=self)

    def _copy_context_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _copy_context_filename(self) -> None:
        path = self._context_path()
        if path is not None:
            self._copy_context_text(path.name)

    def _copy_context_directory_name(self) -> None:
        path = self._context_path()
        if path is not None:
            self._copy_context_text(path.parent.name)

    def add_files_dialog(self) -> None:
        selected = filedialog.askopenfilenames(
            parent=self, title="Add files to Rename",
            filetypes=(("Supported video files", " ".join(f"*{item}" for item in sorted(VIDEO_EXTENSIONS))),
                       ("All files", "*.*")))
        self.add_paths([Path(item) for item in selected])

    def browse_input(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Choose Rename input folder",
            initialdir=self.input_var.get() or None)
        if selected:
            self.input_var.set(selected)

    def load_input_folder(self) -> None:
        folder = Path(self.input_var.get()).expanduser()
        if not folder.is_dir():
            messagebox.showerror("Invalid folder", "Choose an existing input folder.", parent=self)
            return
        iterator = (folder.rglob("*") if self.config_data.include_subfolders
                    else folder.iterdir())
        self.add_paths([path for path in iterator
                        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS])

    def add_folder_dialog(self) -> None:
        initial = self.input_var.get() or None
        selected = filedialog.askdirectory(parent=self, title="Add folder to Rename", initialdir=initial)
        if not selected:
            return
        folder = Path(selected).resolve()
        include = messagebox.askyesno(
            "Include subfolders?", "Also add supported files from all subfolders?",
            parent=self)
        self.input_var.set(str(folder))
        self.config_data.default_input_directory = str(folder)
        self.config_data.include_subfolders = include
        try:
            save_rename_config(self.config_data)
        except OSError:
            pass
        iterator = folder.rglob("*") if include else folder.iterdir()
        self.add_paths([path for path in iterator
                        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS])

    def add_paths(self, paths: list[Path], warn_busy: bool = True,
                  trusted: bool = False) -> int:
        candidates: list[Path] = []
        busy: list[tuple[Path, str]] = []
        for raw_path in paths:
            path = Path(raw_path) if trusted else raw_path.resolve()
            if ((not trusted and not path.is_file())
                    or path.suffix.lower() not in VIDEO_EXTENSIONS):
                continue
            blocked, reason = self.busy_callback(path)
            if blocked:
                busy.append((path, reason))
            elif str(path) not in self.paths:
                candidates.append(path)
        if busy and warn_busy:
            names = "\n".join(f"• {path.name} — {reason}" for path, reason in busy[:12])
            if len(busy) > 12:
                names += f"\n• and {len(busy) - 12} more"
            if not candidates:
                messagebox.showwarning("Files unavailable", f"These files cannot be renamed right now:\n\n{names}", parent=self)
                return 0
            if not messagebox.askokcancel(
                    "Exclude busy files",
                    f"{len(busy)} file(s) are busy and will be excluded:\n\n{names}\n\n"
                    f"Add the remaining {len(candidates)} available file(s)?", parent=self):
                return 0
        for path in candidates:
            iid = str(path)
            self.paths[iid] = path
            self.table.insert("", "end", iid=iid,
                              values=("☑", path.name, path.name, str(path.parent), "Ready"))
        self.refresh_preview()
        self.status_var.set(f"{len(candidates)} file(s) added; {len(self.paths)} total.")
        if candidates:
            RenameGUI._append_activity_log(
                self, f"Added {len(candidates)} file(s) to Rename.")
        return len(candidates)

    def checked_paths(self) -> list[Path]:
        return [self.paths[iid] for iid in self.table.get_children()
                if self.table.item(iid, "values")[0] == "☑"]

    def refresh_preview(self) -> None:
        try:
            options = self.options()
        except tk.TclError:
            return
        checked_index = 0
        proposed: list[Path] = []
        for iid in self.table.get_children():
            path = self.paths[iid]
            values = list(self.table.item(iid, "values"))
            if values[0] == "☑":
                destination = proposed_path(path, options, checked_index)
                checked_index += 1
                values[2] = destination.name
                proposed.append(destination)
            else:
                values[2] = path.name
            blocked, reason = self.busy_callback(path)
            values[4] = f"Busy — {reason}" if blocked else ("Changed" if values[2] != path.name else "Ready")
            self.table.item(iid, values=values)

    def set_all(self, checked: bool) -> None:
        for iid in self.table.get_children():
            values = list(self.table.item(iid, "values"))
            values[0] = "☑" if checked else "☐"
            self.table.item(iid, values=values)
        self.refresh_preview()

    def set_selected(self, checked: bool) -> None:
        for iid in self.table.selection():
            values = list(self.table.item(iid, "values"))
            values[0] = "☑" if checked else "☐"
            self.table.item(iid, values=values)
        self.refresh_preview()

    def remove_selected(self) -> None:
        removed = len(self.table.selection())
        for iid in self.table.selection():
            self.paths.pop(iid, None)
            self.table.delete(iid)
        self.refresh_preview()
        if removed:
            RenameGUI._append_activity_log(self, f"Removed {removed} selected file(s).")

    def remove_checked(self) -> None:
        checked = [iid for iid in self.table.get_children()
                   if self.table.item(iid, "values")[0] == "☑"]
        for iid in checked:
            self.paths.pop(iid, None)
            self.table.delete(iid)
        self.refresh_preview()
        if checked:
            RenameGUI._append_activity_log(self, f"Removed {len(checked)} checked file(s).")

    def remove_all(self) -> None:
        removed = len(self.table.get_children())
        for iid in self.table.get_children():
            self.paths.pop(iid, None)
            self.table.delete(iid)
        self.refresh_preview()
        if removed:
            RenameGUI._append_activity_log(self, f"Removed all {removed} file(s).")

    def reset_controls(self) -> None:
        self.find_var.set(""); self.replace_var.set("")
        self.case_sensitive_var.set(False); self.prefix_var.set(""); self.suffix_var.set("")
        self.remove_start_var.set(0); self.remove_end_var.set(0)
        self.case_mode_var.set("Unchanged"); self.normalize_episode_var.set(False)
        self.preserve_resolution_case_var.set(False)
        self.convert_x_var.set(False); self.spaces_to_dots_var.set(False)
        self.numbering_var.set(False)
        self.number_start_var.set(1); self.number_width_var.set(2)

    def apply_renames(self) -> None:
        checked = self.checked_paths()
        if not checked:
            messagebox.showinfo("Rename Files", "Check one or more files first.", parent=self)
            return
        self._apply_paths(checked)

    def apply_selected_rename(self) -> None:
        """Rename only the focused/highlighted row, independent of checkboxes."""
        row = self.table.focus()
        if not row:
            selected = self.table.selection()
            row = selected[0] if selected else ""
        path = self.paths.get(row)
        if path is None:
            messagebox.showinfo("Rename Selected File", "Select one file in the list first.", parent=self)
            return
        self._apply_paths([path])

    def _apply_paths(self, paths: list[Path]) -> None:
        busy = [(path, self.busy_callback(path)[1]) for path in paths
                if self.busy_callback(path)[0]]
        available = [path for path in paths if path not in {item[0] for item in busy}]
        if busy:
            names = "\n".join(f"• {path.name} — {reason}" for path, reason in busy)
            if not available:
                messagebox.showwarning(
                    "File unavailable",
                    f"The selected file cannot be renamed right now:\n\n{names}",
                    parent=self)
                return
            if not messagebox.askokcancel(
                    "Exclude busy files",
                    f"The following files became busy and will be excluded:\n\n{names}\n\n"
                    f"Rename the remaining {len(available)} file(s)?", parent=self):
                return
        options = self.options()
        plan = {path: proposed_path(path, options, index)
                for index, path in enumerate(available)}
        changed = {source: destination for source, destination in plan.items()
                   if source.name != destination.name}
        if not changed:
            messagebox.showinfo("Rename Files", "The current controls do not change the selected filename(s).", parent=self)
            return
        preview = "\n".join(f"{old.name}  →  {new.name}" for old, new in list(changed.items())[:15])
        if len(changed) > 15:
            preview += f"\n…and {len(changed) - 15} more"
        try:
            companions = (self.companion_plan_callback(changed)
                          if self.companion_plan_callback else {})
            validate_rename_plan({**changed, **companions})
        except ValueError as exc:
            messagebox.showerror("Cannot rename files", str(exc), parent=self)
            return
        if not messagebox.askyesno("Confirm Rename", f"Rename {len(changed)} file(s)?\n\n{preview}", parent=self):
            return
        try:
            completed_all = execute_rename_plan({**changed, **companions})
            completed = {old: completed_all[old] for old in changed}
            if self.renamed_callback:
                self.renamed_callback(completed)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Rename failed", str(exc), parent=self)
            return
        self.last_rename = completed
        self.undo_button.configure(state="normal")
        self._replace_paths(completed)
        self.status_var.set(f"Renamed {len(completed)} file(s).")
        for old, new in completed.items():
            RenameGUI._append_activity_log(
                self, f"Renamed: {old.name} → {new.name}")

    def _replace_paths(self, mapping: dict[Path, Path]) -> None:
        records: list[tuple[int, str, Path]] = []
        for old, new in mapping.items():
            old_iid = str(old)
            if old_iid not in self.paths:
                continue
            index = self.table.index(old_iid)
            checked = self.table.item(old_iid, "values")[0]
            records.append((index, checked, new))
        for old in mapping:
            old_iid = str(old)
            if old_iid not in self.paths:
                continue
            self.table.delete(old_iid)
            self.paths.pop(old_iid, None)
        for index, checked, new in sorted(records):
            new_iid = str(new)
            self.paths[new_iid] = new
            self.table.insert("", index, iid=new_iid,
                              values=(checked, new.name, new.name, str(new.parent), "Ready"))
        self.refresh_preview()

    def undo(self) -> None:
        if not self.last_rename:
            return
        reverse = {new: old for old, new in self.last_rename.items()}
        try:
            companions = (self.companion_plan_callback(reverse)
                          if self.companion_plan_callback else {})
            validate_rename_plan({**reverse, **companions})
        except ValueError as exc:
            messagebox.showerror("Cannot undo rename", str(exc), parent=self)
            return
        if any(self.busy_callback(path)[0] for path in reverse):
            messagebox.showwarning("Cannot undo rename", "One or more renamed files are currently busy.", parent=self)
            return
        try:
            completed_all = execute_rename_plan({**reverse, **companions})
            completed = {old: completed_all[old] for old in reverse}
            if self.renamed_callback:
                self.renamed_callback(completed)
        except OSError as exc:
            messagebox.showerror("Undo failed", str(exc), parent=self)
            return
        self._replace_paths(completed)
        self.last_rename = {}
        self.undo_button.configure(state="disabled")
        self.status_var.set(f"Restored {len(completed)} filename(s).")
        for old, new in completed.items():
            RenameGUI._append_activity_log(
                self, f"Undo: {old.name} → {new.name}")

    def settings(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Set Default Rename Folder",
            initialdir=self.input_var.get() or None)
        if not selected:
            return
        self.input_var.set(selected)
        self.config_data.default_input_directory = selected
        self.config_data.include_subfolders = messagebox.askyesno(
            "Default Rename Folder",
            "When loading the default folder, include files from its subfolders?",
            parent=self)
        try:
            save_rename_config(self.config_data)
        except OSError as exc:
            messagebox.showerror("Cannot save settings", str(exc), parent=self)

