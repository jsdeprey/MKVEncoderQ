from __future__ import annotations

import os
import threading
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import messagebox, ttk

from . import __version__
from .config import application_directory, load_config
from .gui import OptimizerGUI
from .rename_gui import RenameGUI
from .scanner import VIDEO_EXTENSIONS
from mkvcleaner_gui import CleanerGUI

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # Source runs without DnD; build.bat bundles this dependency.
    DND_FILES = None
    RootWindow = tk.Tk
else:
    RootWindow = TkinterDnD.Tk


class CleanCoordinator:
    """Track safe handoffs from Encoder paths to Cleaner paths."""

    def __init__(self) -> None:
        self.pending: set[Path] = set()
        self.pending_display: dict[Path, Path] = {}
        self.ready: deque[Path] = deque()
        self.visible: dict[Path, tuple[str, str]] = {}
        self._lock = threading.RLock()

    def request(self, source: Path, status: str, encoder_running: bool,
                completed_output: Path | None = None,
                planned_output: Path | None = None) -> None:
        source = source.resolve()
        with self._lock:
            if completed_output is not None and completed_output.is_file():
                output = completed_output.resolve()
                self.ready.append(output)
                self.visible[output] = ("Queued", "Waiting for Cleaner")
            elif (source.suffix.lower() == ".mkv" and source.is_file()
                  and (not encoder_running or status in {
                      "Completed", "Skipped", "Failed", "Cancelled"})):
                self.ready.append(source)
                self.visible[source] = ("Queued", "Waiting for Cleaner")
            elif status not in {"Completed", "Skipped", "Failed", "Cancelled"}:
                # Non-MKV inputs can only be cleaned after encoding creates and
                # verifies their MKV output. Never send the source or backup.
                self.pending.add(source)
                display = (planned_output or source).resolve()
                self.pending_display[source] = display
                self.visible[display] = (
                    "Waiting for Encode",
                    f"Waiting for {source.name} to finish encoding",
                )
            else:
                self.pending.discard(source)
                display = self.pending_display.pop(source, source)
                self.visible.pop(display, None)

    def output_ready(self, source: Path, output: Path) -> None:
        source = source.resolve()
        with self._lock:
            if source in self.pending:
                self.pending.discard(source)
                display = self.pending_display.pop(source, source)
                self.visible.pop(display, None)
                resolved_output = output.resolve()
                self.ready.append(resolved_output)
                self.visible[resolved_output] = ("Queued", "Waiting for Cleaner")

    def reconcile_completed(self, completed_outputs: dict[Path, Path]) -> None:
        """Recover any Cleaner handoff whose one-time completion event was missed."""
        with self._lock:
            waiting = tuple(self.pending)
        if not waiting:
            return
        normalized = {
            os.path.normcase(str(Path(source).resolve())): (source, output)
            for source, output in completed_outputs.items()
        }
        for pending_source in waiting:
            match = normalized.get(os.path.normcase(str(pending_source.resolve())))
            if match is not None:
                _source, output = match
                if Path(output).is_file():
                    self.output_ready(pending_source, Path(output))

    def status_changed(self, source: Path, status: str) -> None:
        # Encoder queue paths are already canonical.  This callback runs for
        # every row painted during inventory, so resolving each path again can
        # turn a large NAS listing into thousands of blocking network calls on
        # Tk's UI thread.
        source = Path(source)
        with self._lock:
            if source in self.pending and status in {"Skipped", "Failed", "Cancelled"}:
                self.pending.discard(source)
                display = self.pending_display.pop(source, source)
                if source.suffix.lower() == ".mkv" and source.is_file():
                    self.visible.pop(display, None)
                    self.ready.append(source)
                    self.visible[source] = ("Queued", "Waiting for Cleaner")
                else:
                    self.visible[display] = (
                        "Not Cleaned",
                        f"No MKV output — encode {status.lower()}",
                    )

    def drain(self) -> list[Path]:
        unique: list[Path] = []
        seen: set[Path] = set()
        with self._lock:
            while self.ready:
                path = self.ready.popleft().resolve()
                if path not in seen and path.is_file() and path.suffix.lower() == ".mkv":
                    seen.add(path)
                    unique.append(path)
        return unique

    def visible_snapshot(self) -> dict[Path, tuple[str, str]]:
        with self._lock:
            return dict(self.visible)

    def mark_started(self, paths: list[Path]) -> None:
        with self._lock:
            for path in paths:
                self.visible.pop(path.resolve(), None)

    def cancel(self, path: Path) -> None:
        """Remove one pending or ready Cleaner request."""
        path = path.resolve()
        with self._lock:
            self.visible.pop(path, None)
            self.ready = deque(item for item in self.ready
                               if item.resolve() != path)
            for source, display in list(self.pending_display.items()):
                if source == path or display == path:
                    self.pending.discard(source)
                    self.pending_display.pop(source, None)
                    self.visible.pop(display, None)

    def paths_renamed(self, mapping: dict[Path, Path]) -> None:
        """Migrate pending and ready Cleaner handoffs after a disk rename."""
        resolved = {Path(old): Path(new) for old, new in mapping.items()}
        with self._lock:
            previous_display = dict(self.pending_display)
            new_display: dict[Path, Path] = {}
            display_map: dict[Path, Path] = {}
            for source, display in previous_display.items():
                renamed_source = resolved.get(source, source)
                renamed_display = resolved.get(display)
                if renamed_display is None:
                    renamed_display = (display.with_name(
                        renamed_source.stem + display.suffix)
                        if renamed_source != source else display)
                new_display[renamed_source] = renamed_display
                display_map[display] = renamed_display
            self.pending = {resolved.get(source, source) for source in self.pending}
            self.pending_display = new_display
            self.ready = deque(resolved.get(item, item)
                               for item in self.ready)
            new_visible: dict[Path, tuple[str, str]] = {}
            for path, state in self.visible.items():
                destination = display_map.get(path, resolved.get(path, path))
                if state[0] == "Waiting for Encode":
                    source = next((item for item, display in new_display.items()
                                   if display == destination), None)
                    detail = (f"Waiting for {source.name} to finish encoding"
                              if source is not None else state[1])
                    new_visible[destination] = (state[0], detail)
                else:
                    new_visible[destination] = state
            self.visible = new_visible


class CombinedApp(RootWindow):
    """One window containing the Encoder and Cleaner applications."""

    def __init__(self) -> None:
        super().__init__()
        os.environ["MKVCLEANER_GUI"] = "1"
        self.title(f"MKV Encoder Q {__version__}")
        self.geometry("1360x850")
        self.minsize(980, 680)
        self.clean_coordinator = CleanCoordinator()
        self.cleaner_reserved_paths: set[Path] = set()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        encoder_tab = ttk.Frame(notebook)
        cleaner_tab = ttk.Frame(notebook)
        rename_tab = ttk.Frame(notebook)
        notebook.add(encoder_tab, text="Encoder")
        notebook.add(cleaner_tab, text="Cleaner")
        notebook.add(rename_tab, text="Rename")

        self.cleaner = None
        self.encoder = OptimizerGUI(
            encoder_tab,
            clean_request_callback=self.request_clean,
            output_ready_callback=self.encoder_output_ready,
            status_callback=self.encoder_status_changed,
            rename_request_callback=self.send_to_rename,
            rename_directory_callback=self.send_directory_to_rename,
            inventory_state_callback=self.set_inventory_tabs_locked,
        )
        self.encoder.pack(fill="both", expand=True)
        self.cleaner = CleanerGUI(
            cleaner_tab,
            finished_callback=self.cleaner_finished,
            tool_availability_callback=self.cleaner_tool_availability_changed,
            rename_guard_callback=self.cleaner_rename_guard,
            queue_remove_callback=self.remove_clean_request,
            start_guard_callback=self.cleaner_start_guard,
            rename_request_callback=self.send_to_rename,
            rename_directory_callback=self.send_directory_to_rename,
        )
        self.cleaner.pack(fill="both", expand=True)
        self.renamer = RenameGUI(
            rename_tab, busy_callback=self.rename_busy,
            renamed_callback=self.paths_renamed,
            companion_plan_callback=self.rename_companion_plan)
        self.renamer.pack(fill="both", expand=True)
        self.notebook = notebook
        self.encoder_tab = encoder_tab
        self.cleaner_tab = cleaner_tab
        self.rename_tab = rename_tab
        notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.encoder.apply_theme(self.encoder.theme_name)
        self._enable_drop_targets()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(500, self._poll_ready_clean)

    def set_inventory_tabs_locked(self, locked: bool) -> None:
        """Keep independent tabs available during background inventory."""
        for tab in (self.cleaner_tab, self.rename_tab):
            # Inventory runs in the background.  Disabling either tab can both
            # block independent work and make ttk.Notebook jump to Encoder.
            self.notebook.tab(tab, state="normal")

    def _enable_drop_targets(self) -> None:
        """Accept Explorer file/folder drops on all three file lists."""
        if DND_FILES is None:
            return
        self.encoder.table.drop_target_register(DND_FILES)
        self.encoder.table.dnd_bind("<<Drop>>", self._drop_encoder)
        self.cleaner.file_tree.drop_target_register(DND_FILES)
        self.cleaner.file_tree.dnd_bind("<<Drop>>", self._drop_cleaner)
        self.renamer.table.drop_target_register(DND_FILES)
        self.renamer.table.dnd_bind("<<Drop>>", self._drop_renamer)

    def _drop_paths(self, event) -> list[str]:
        try:
            return list(self.tk.splitlist(event.data))
        except (AttributeError, tk.TclError):
            return []

    def _drop_encoder(self, event):
        self.encoder.add_dropped_paths(self._drop_paths(event))
        return getattr(event, "action", "copy")

    def _drop_cleaner(self, event):
        self.cleaner.add_dropped_paths(self._drop_paths(event))
        return getattr(event, "action", "copy")

    def _drop_renamer(self, event):
        paths: list[Path] = []
        for raw in self._drop_paths(event):
            candidate = Path(raw).resolve()
            if candidate.is_dir():
                paths.extend(path for path in candidate.rglob("*")
                             if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS)
            else:
                paths.append(candidate)
        self.renamer.add_paths(paths)
        self.notebook.select(self.rename_tab)
        return getattr(event, "action", "copy")

    def _tab_changed(self, _event=None) -> None:
        selected = self.notebook.select()
        self.encoder.set_background_ui(selected != str(self.encoder_tab))
        if selected == str(self.cleaner_tab):
            self.cleaner.apply_theme(self.cleaner.config.theme)
            self.cleaner.refresh_tool_availability()
        elif selected == str(self.encoder_tab):
            self.encoder.apply_theme(self.encoder.theme_name)

    def send_to_rename(self, paths: list[Path]) -> None:
        # Show the requested workspace immediately and throttle Encoder's bulk
        # inventory painting before touching even a known network path.
        self.encoder.set_background_ui(True)
        self.notebook.select(self.rename_tab)
        known_paths = [Path(path) for path in paths]
        self.after_idle(lambda: self.renamer.add_paths(
            known_paths, trusted=True))

    def send_directory_to_rename(self, folder: Path) -> None:
        choice = messagebox.askyesnocancel(
            "Add directory to Rename",
            f"Add supported files from:\n{folder}\n\nInclude subfolders?\n\n"
            "Yes = include subfolders\nNo = this folder only",
            parent=self)
        if choice is None:
            return
        iterator = folder.rglob("*") if choice else folder.iterdir()
        paths = [path for path in iterator
                 if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS]
        self.renamer.add_paths(paths)
        self.notebook.select(self.rename_tab)

    def rename_busy(self, path: Path) -> tuple[bool, str]:
        """Return why a path must not be renamed at this moment."""
        # Encoder and Rename already exchange canonical queue paths. Resolving
        # one again can block Tk on a busy NAS during initial inventory.
        path = Path(path)
        inspection_lock = getattr(self.encoder, "inspection_lock", None)
        if inspection_lock is None:
            inspection_active = getattr(
                self.encoder, "inspection_active_path", None)
        else:
            with inspection_lock:
                inspection_active = getattr(
                    self.encoder, "inspection_active_path", None)
        if inspection_active is not None and Path(inspection_active) == path:
            return True, "Media inspection in progress"
        if self.encoder.live is not None:
            for item in self.encoder.live.snapshot():
                represented = self.encoder.completed_outputs.get(
                    item.path, item.path)
                if Path(represented) == path and item.status in {
                        "Sampling", "Encoding", "Paused"}:
                    return True, item.status
        active_cleaner = {
            "Inspecting", "Will clean", "Will remux", "Cleaning",
            "Creating backup", "Editing metadata", "Remuxing",
            "Replacing original", "Verifying", "Waiting (paused)",
        }
        for paths in (self.cleaner.row_ids, self.cleaner.queued_row_ids):
            row = paths.get(path)
            if row and self.cleaner.file_tree.exists(row):
                values = self.cleaner.file_tree.item(row, "values")
                status = str(values[2]) if len(values) > 2 else ""
                if status in active_cleaner:
                    return True, status
        return False, ""

    def paths_renamed(self, mapping: dict[Path, Path]) -> None:
        """Synchronize a successful Rename transaction across every tab."""
        self.clean_coordinator.paths_renamed(mapping)
        self.encoder.paths_renamed(mapping)
        self.cleaner.paths_renamed(mapping)
        self.cleaner.sync_external_queue(self.clean_coordinator.visible_snapshot())

    def rename_companion_plan(self, mapping: dict[Path, Path]) -> dict[Path, Path]:
        """Rename an existing Encoder backup alongside its current video."""
        suffix = load_config(None).backup_suffix
        companions: dict[Path, Path] = {}
        completed_outputs = getattr(
            getattr(self, "encoder", None), "completed_outputs", {})
        for old, new in mapping.items():
            backup_source = next(
                (source for source, output in completed_outputs.items()
                 if Path(output) == Path(old)),
                old,
            )
            old_backup = Path(backup_source).with_name(
                Path(backup_source).name + suffix).resolve()
            if old_backup.is_file():
                companions[old_backup] = new.with_name(new.name + suffix).resolve()
        return companions

    def cleaner_tool_availability_changed(self, available: bool) -> None:
        """Keep Encoder cleaning controls in sync without disabling encoding."""
        self.encoder.set_clean_available(available)

    def cleaner_rename_guard(self, paths: list[Path]) -> tuple[bool, str]:
        """Prevent Cleaner renames from invalidating Encoder queue state."""
        tracked: set[Path] = set()
        if self.encoder.live is not None:
            tracked.update(self.encoder.live.items)
            tracked.update(self.encoder.live.completed)
        tracked.update(self.encoder.completed_outputs)
        tracked.update(self.encoder.completed_outputs.values())
        conflicts = [path.resolve() for path in paths if path.resolve() in tracked]
        if conflicts:
            names = ", ".join(path.name for path in conflicts[:3])
            if len(conflicts) > 3:
                names += f", and {len(conflicts) - 3} more"
            return False, (
                "These files are currently tracked by Encoder and cannot be renamed "
                f"from Cleaner: {names}\n\nRemove them from the Encoder list or "
                "reopen the application before renaming them.")
        return True, ""

    def remove_clean_request(self, path: Path) -> None:
        self.clean_coordinator.cancel(path)

    def request_clean(self, paths: list[Path], switch_tab: bool = True) -> None:
        for path in paths:
            source = path.resolve()
            output = self.encoder.completed_outputs.get(source)
            status = ""
            iid = str(source)
            if self.encoder.table.exists(iid):
                status = str(self.encoder.table.set(iid, "status"))
            self.clean_coordinator.request(
                source, status, self.encoder.running, output,
                self._planned_output(source))
        self.cleaner.sync_external_queue(self.clean_coordinator.visible_snapshot())
        self._dispatch_ready_clean()
        if switch_tab:
            self.notebook.select(self.cleaner_tab)

    def _planned_output(self, source: Path) -> Path:
        """Return the filename Cleaner will receive if Encoder succeeds."""
        try:
            config = getattr(self.encoder, "run_config", None) or load_config(None)
            root = Path(self.encoder.input_var.get() or config.input_directory)
            if not root.is_absolute():
                root = application_directory() / root
            root = root.resolve()
            output = Path(config.output_directory) if config.output_directory.strip() else root
            if not output.is_absolute():
                output = application_directory() / output
            return (output.resolve() / source.resolve().relative_to(root)).with_suffix(".mkv")
        except (OSError, ValueError):
            return source.with_suffix(".mkv").resolve()

    def encoder_output_ready(self, source: Path, output: Path) -> None:
        self.clean_coordinator.output_ready(source, output)

    def encoder_status_changed(self, source: Path, status: str) -> None:
        self.clean_coordinator.status_changed(source, status)

    def cleaner_finished(self, _results) -> None:
        if self.encoder.live is not None and self.cleaner_reserved_paths:
            released = self.encoder.live.release_cleaning(
                list(self.cleaner_reserved_paths))
            for path in released:
                item = self.encoder.live.items.get(path)
                if item is not None:
                    detail = (
                        "Media inspected — Ready"
                        if path in self.encoder.inspection_results
                        and item.status == "Waiting"
                        else item.detail
                    )
                    self.encoder._emit(
                        "row", path, item.status, "—", "—",
                        str(self.encoder.table.set(str(path), "current_size"))
                        if self.encoder.table.exists(str(path)) else "—",
                        "—", detail)
            self.cleaner_reserved_paths.clear()
        self.after(100, self._dispatch_ready_clean)

    def cleaner_start_guard(self, paths: list[Path]) -> tuple[bool, str]:
        """Reserve waiting Encoder inputs or reject files already active."""
        if self.encoder.live is None:
            return True, ""
        allowed, reserved, reason = self.encoder.live.reserve_for_cleaning(paths)
        if not allowed:
            return False, (
                f"{reason.capitalize()}. Wait for Encoder to finish that file "
                "before cleaning it.")
        self.cleaner_reserved_paths.update(reserved)
        for path in reserved:
            item = self.encoder.live.items.get(path)
            if item is not None:
                self.encoder._emit(
                    "row", path, item.status, "—", "—",
                    str(self.encoder.table.set(str(path), "current_size"))
                    if self.encoder.table.exists(str(path)) else "—",
                    "—", "Waiting — being cleaned now")
        return True, ""

    def _poll_ready_clean(self) -> None:
        self.clean_coordinator.reconcile_completed(
            self.encoder.completed_outputs)
        self.cleaner.sync_external_queue(self.clean_coordinator.visible_snapshot())
        self._dispatch_ready_clean()
        self.after(500, self._poll_ready_clean)

    def _dispatch_ready_clean(self) -> None:
        if self.cleaner is not None and not self.cleaner.running:
            unique = self.clean_coordinator.drain()
            if unique:
                if self.cleaner.start_paths(unique):
                    self.clean_coordinator.mark_started(unique)
                    self.cleaner.sync_external_queue(
                        self.clean_coordinator.visible_snapshot())
                else:
                    self.clean_coordinator.ready.extendleft(reversed(unique))

    def close(self) -> None:
        if self.encoder.has_active_work() or (self.cleaner and self.cleaner.running):
            messagebox.showinfo(
                "Work in progress",
                "Wait for active sampling, encoding, or cleaning to finish, "
                "or stop it before closing the application.",
                parent=self,
            )
            return
        if self.encoder.running:
            self.encoder.stop_event.set()
        self.destroy()


def main() -> int:
    CombinedApp().mainloop()
    return 0
