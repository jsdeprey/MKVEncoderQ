from __future__ import annotations

import time
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .scanner import discover, discover_inventory


@dataclass
class FileState:
    path: Path
    status: str = "Checking file..."
    size: int = 0
    modified_ns: int = 0
    unchanged_since: float = 0.0
    detail: str = "Waiting for file to finish copying"
    force_encode: bool = False
    selected: bool = True
    status_before_excluded: str = "Waiting"
    status_before_missing: str = "Waiting"
    detail_before_missing: str = "Ready"


class LiveQueue:
    """Session queue with conservative file-copy readiness detection."""

    def __init__(self, root: Path, ready_seconds: float,
                 excluded_root: Path | None = None,
                 allowed_paths: set[Path] | None = None,
                 default_selected: bool = True) -> None:
        self.root = root
        self.ready_seconds = ready_seconds
        self.excluded_root = excluded_root
        self.allowed_paths = ({path.resolve() for path in allowed_paths}
                              if allowed_paths is not None else None)
        self.items: dict[Path, FileState] = {}
        self.completed: set[Path] = set()
        self.reanalysis_queue: deque[Path] = deque()
        self.manual_roots: dict[Path, Path] = {}
        self.cleaning_reserved: set[Path] = set()
        # Incremental NAS inventory releases the queue lock while walking.
        # Rename may therefore migrate an item after the walker recorded its
        # old path.  Recheck only those destinations before declaring them
        # missing when that stale inventory completes.
        self.recently_renamed: set[Path] = set()
        self.default_selected = default_selected
        self.last_discovered_count = 0
        self._lock = threading.RLock()

    def _known_or_resolved(self, path: Path) -> Path:
        """Reuse canonical queue keys without another potentially remote stat."""
        path = Path(path)
        if (path in self.items or path in self.completed
                or path in self.manual_roots):
            return path
        return path.resolve()

    def refresh_incremental(
            self, now: float | None = None, batch_size: int = 75,
            batch_callback: Callable[[list[FileState], int], None] | None = None,
            discovery_callback: Callable[[str, int, int, int], None] | None = None,
            cancelled: Callable[[], bool] | None = None,
            existence_paths: dict[Path, Path] | None = None,
            readiness_enabled: bool = True,
            folder_delay_seconds: float = 0.0) -> list[FileState]:
        """Discover a large tree in batches without holding the queue lock."""
        now = time.monotonic() if now is None else now
        discovered: set[Path] = set()
        newly_ready: list[FileState] = []
        pending: list[Path] = []
        inventory_metadata: dict[Path, tuple[int, int]] = {}

        def ingest(paths: list[Path]) -> None:
            visible: list[FileState] = []
            with self._lock:
                for raw_path in paths:
                    # discover_inventory starts from a canonical root and
                    # os.scandir returns children beneath that root.  Resolving
                    # every child repeats a remote lookup for large NAS trees.
                    path = Path(raw_path)
                    discovered.add(path)
                    if self.allowed_paths is not None and path not in self.allowed_paths:
                        continue
                    if path in self.completed:
                        continue
                    cached_stat = inventory_metadata.pop(path, None)
                    if cached_stat is None:
                        try:
                            stat = path.stat()
                            size, modified_ns = stat.st_size, stat.st_mtime_ns
                        except OSError:
                            continue
                    else:
                        size, modified_ns = cached_stat
                    item = self.items.get(path)
                    if item is None:
                        # Inventory only records a stable directory snapshot.
                        # Start performs the real copy-stability check before
                        # sampling/encoding, so a second UI-wide ready pass is
                        # unnecessary here.
                        normal_status = ("Waiting" if readiness_enabled
                                         else "Waiting to scan")
                        status = normal_status if self.default_selected else "Not selected"
                        detail = (("Ready" if readiness_enabled else
                                   "Detected — stability check waits for current encode")
                                  if self.default_selected else
                                  "Excluded from processing")
                        item = FileState(
                            path, status=status, size=size,
                            modified_ns=modified_ns, unchanged_since=now,
                            detail=detail, selected=self.default_selected,
                            status_before_excluded=normal_status)
                        self.items[path] = item
                    elif item.status == "Missing":
                        item.status = item.status_before_missing
                        item.detail = item.detail_before_missing
                        item.size = size
                        item.modified_ns = modified_ns
                        item.unchanged_since = now
                    elif item.status == "Waiting to scan" and readiness_enabled:
                        item.status = "Checking file..."
                        item.detail = "Waiting for file to finish copying"
                        item.unchanged_since = now
                    elif ((item.size, item.modified_ns)
                          != (size, modified_ns)
                          and item.status in {"Checking file...", "Waiting"}):
                        item.size = size
                        item.modified_ns = modified_ns
                        item.unchanged_since = now
                        item.status = "Checking file..."
                    if (item.status == "Checking file..."
                            and now - item.unchanged_since >= self.ready_seconds):
                        item.status = "Waiting"
                        item.detail = "Ready"
                        newly_ready.append(item)
                    visible.append(item)
            if batch_callback and visible:
                batch_callback(visible, len(discovered))

        last_progress_at = 0.0
        last_progress_folder = 0
        latest_progress = (0, 0, 0)

        def report_progress(phase: str, folders: int, files: int,
                            top_level: int) -> None:
            nonlocal last_progress_at, last_progress_folder
            nonlocal latest_progress
            latest_progress = (folders, files, top_level)
            current = time.monotonic()
            if (phase == "base" or folders - last_progress_folder >= 25
                    or current - last_progress_at >= 0.25):
                last_progress_at = current
                last_progress_folder = folders
                if discovery_callback:
                    discovery_callback(phase, folders, files, top_level)

        for path in discover_inventory(
                self.root, "_video_optimizer_backups", self.excluded_root,
                report_progress, cancelled,
                lambda path, size, modified: inventory_metadata.__setitem__(
                    Path(path), (size, modified)), folder_delay_seconds):
            pending.append(path)
            if len(pending) >= max(1, batch_size):
                ingest(pending)
                pending = []
        if pending:
            ingest(pending)
        if discovery_callback:
            discovery_callback("complete", *latest_progress)

        # A cancelled recursive walk is only a partial snapshot.  Never use it
        # to mark the files that were not reached as Missing; the last complete
        # inventory remains authoritative until a later scan finishes.
        if cancelled is not None and cancelled():
            return newly_ready

        manual = []
        with self._lock:
            manual = [path for path in self.manual_roots if path not in discovered]
        if manual:
            ingest(manual)

        tracked = {source.resolve(): output.resolve()
                   for source, output in (existence_paths or {}).items()}
        with self._lock:
            for path, item in self.items.items():
                actual_path = tracked.get(path, path)
                if item.status == "Missing":
                    if actual_path.is_file():
                        item.status = item.status_before_missing
                        item.detail = item.detail_before_missing
                    continue
                if item.status in {"Sampling", "Encoding", "Paused"}:
                    continue
                present = (path in discovered or path in self.manual_roots
                           or (path in self.completed and actual_path.is_file()))
                if (not present and path in self.recently_renamed
                        and path.is_file()):
                    present = True
                if not present:
                    item.status_before_missing = item.status
                    item.detail_before_missing = item.detail
                    item.status = "Missing"
                    item.detail = "File is no longer in the scanned directory"
            self.last_discovered_count = len(discovered)
            self.recently_renamed.clear()
        return newly_ready

    def refresh(self, now: float | None = None,
                readiness_enabled: bool = True,
                discover_new: bool = True,
                existence_paths: dict[Path, Path] | None = None) -> list[FileState]:
        """Discover files, advancing stability checks only when enabled."""
        now = time.monotonic() if now is None else now
        newly_ready: list[FileState] = []
        with self._lock:
          paths = (list(discover(
              self.root, "_video_optimizer_backups", self.excluded_root))
              if discover_new else list(self.items))
          if discover_new:
              known = {path.resolve() for path in paths}
              paths.extend(path for path in self.manual_roots if path not in known)
          discovered = {path.resolve() for path in paths}
          for path in paths:
            path = path.resolve()
            if self.allowed_paths is not None and path not in self.allowed_paths:
                continue
            if path in self.completed:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            item = self.items.get(path)
            if item is None:
                normal_status = "Checking file..." if readiness_enabled else "Waiting to scan"
                status = normal_status if self.default_selected else "Not selected"
                detail = ("Waiting for file to finish copying" if readiness_enabled
                          else "Detected — scan waits for current encode")
                if not self.default_selected:
                    detail = "Excluded from processing"
                item = FileState(path, status=status, size=stat.st_size,
                                 modified_ns=stat.st_mtime_ns,
                                 unchanged_since=now, detail=detail,
                                 selected=self.default_selected,
                                 status_before_excluded=normal_status)
                self.items[path] = item
            elif item.status == "Missing":
                restored = item.status_before_missing
                if restored in {"Waiting", "Checking file...", "Waiting to scan"}:
                    item.status = "Checking file..." if readiness_enabled else "Waiting to scan"
                    item.detail = ("Waiting for file to finish copying" if readiness_enabled
                                   else "Detected — scan waits for current encode")
                else:
                    item.status = restored
                    item.detail = item.detail_before_missing
                item.size = stat.st_size
                item.modified_ns = stat.st_mtime_ns
                item.unchanged_since = now
                if restored == "Queued" and path not in self.reanalysis_queue:
                    self.reanalysis_queue.append(path)
            elif item.status == "Waiting to scan" and readiness_enabled:
                item.size = stat.st_size
                item.modified_ns = stat.st_mtime_ns
                item.unchanged_since = now
                item.status = "Checking file..."
                item.detail = "Waiting for file to finish copying"
            elif (item.size, item.modified_ns) != (stat.st_size, stat.st_mtime_ns) \
                    and item.status in {"Checking file...", "Waiting"}:
                item.size = stat.st_size
                item.modified_ns = stat.st_mtime_ns
                item.unchanged_since = now
                item.status = "Checking file..."
            if (readiness_enabled and item.status == "Checking file..."
                    and now - item.unchanged_since >= self.ready_seconds):
                item.status = "Waiting"
                item.detail = "Ready"
                newly_ready.append(item)
          tracked = {source.resolve(): output.resolve()
                     for source, output in (existence_paths or {}).items()}
          for path, item in self.items.items():
            actual_path = tracked.get(path, path)
            if item.status == "Missing":
                if actual_path.is_file():
                    item.status = item.status_before_missing
                    item.detail = item.detail_before_missing
                    try:
                        stat = actual_path.stat()
                        item.size = stat.st_size
                        item.modified_ns = stat.st_mtime_ns
                    except OSError:
                        pass
                continue
            if item.status in {"Sampling", "Encoding", "Paused"}:
                continue
            present = actual_path.is_file() and (
                path in self.completed or path in discovered
                or path in self.manual_roots)
            if not present:
                item.status_before_missing = item.status
                item.detail_before_missing = item.detail
                item.status = "Missing"
                item.detail = "File is no longer in the scanned directory"
        return newly_ready

    def finalize_inventory(self) -> None:
        """Make a completed inventory selectable; Start rechecks stability."""
        with self._lock:
            for item in self.items.values():
                if item.status == "Checking file...":
                    item.status = "Waiting"
                    item.detail = "Ready"
                    item.status_before_excluded = "Waiting"

    def add_manual(self, entries: list[tuple[Path, Path]],
                   now: float | None = None) -> list[FileState]:
        """Add explicitly selected files that may live outside the scan root."""
        now = time.monotonic() if now is None else now
        added: list[FileState] = []
        with self._lock:
            for raw_path, raw_root in entries:
                path = raw_path.resolve()
                if path in self.items or path in self.completed or not path.is_file():
                    continue
                normal_status = "Checking file..."
                status = normal_status if self.default_selected else "Not selected"
                detail = ("Waiting for file to finish copying" if self.default_selected
                          else "Excluded from processing")
                stat = path.stat()
                item = FileState(
                    path, status=status, size=stat.st_size,
                    modified_ns=stat.st_mtime_ns, unchanged_since=now,
                    detail=detail, selected=self.default_selected,
                    status_before_excluded=normal_status,
                )
                self.items[path] = item
                self.manual_roots[path] = raw_root.resolve()
                added.append(item)
        return added

    def source_root(self, path: Path) -> Path:
        with self._lock:
            return self.manual_roots.get(self._known_or_resolved(path), self.root)

    def rename_path(self, old: Path, new: Path) -> None:
        """Migrate all queue references after an externally coordinated rename."""
        self.rename_paths({old: new})

    def rename_paths(self, mapping: dict[Path, Path]) -> None:
        """Atomically migrate queue references for a rename batch or swap."""
        # Rename coordination passes the same canonical Path objects already
        # used by the live queue. Resolving them again can synchronously touch
        # every parent on a NAS while Tk waits for this migration to finish.
        resolved = {Path(old): Path(new) for old, new in mapping.items()}
        with self._lock:
            reordered: dict[Path, FileState] = {}
            for path, item in self.items.items():
                destination = resolved.get(path, path)
                item.path = destination
                reordered[destination] = item
            self.items = reordered
            self.completed = {resolved.get(path, path) for path in self.completed}
            self.manual_roots = {
                resolved.get(path, path): resolved.get(root, root)
                for path, root in self.manual_roots.items()
            }
            if self.allowed_paths is not None:
                self.allowed_paths = {
                    resolved.get(path, path) for path in self.allowed_paths}
            self.reanalysis_queue = deque(
                resolved.get(path, path) for path in self.reanalysis_queue)
            self.cleaning_reserved = {
                resolved.get(path, path) for path in self.cleaning_reserved}
            self.recently_renamed.update(resolved.values())

    def dismiss(self, path: Path) -> None:
        """Hide a path and keep it from being rediscovered this session."""
        with self._lock:
            path = self._known_or_resolved(path)
            self.completed.add(path)
            self.items.pop(path, None)
            self.manual_roots.pop(path, None)
            self.cleaning_reserved.discard(path)
            self.reanalysis_queue = deque(
                queued for queued in self.reanalysis_queue if queued != path)

    def snapshot(self) -> list[FileState]:
        with self._lock:
            return list(self.items.values())

    def claim_waiting(self) -> FileState | None:
        with self._lock:
            item = None
            deferred: list[Path] = []
            while self.reanalysis_queue and item is None:
                path = self.reanalysis_queue.popleft()
                if path in self.cleaning_reserved:
                    deferred.append(path)
                    continue
                candidate = self.items.get(path)
                if candidate is not None and candidate.status == "Queued":
                    item = candidate
            self.reanalysis_queue.extendleft(reversed(deferred))
            if item is None:
                item = next((x for x in self.items.values()
                             if x.status == "Waiting" and x.selected
                             and x.path not in self.cleaning_reserved), None)
            if item is not None:
                item.status = "Sampling"
            return item

    def reserve_for_cleaning(self, paths: list[Path]) -> tuple[bool, list[Path], str]:
        """Atomically protect idle Encoder files while Cleaner edits them."""
        with self._lock:
            known = [self._known_or_resolved(path) for path in paths]
            active = [path for path in known
                      if path in self.items and self.items[path].status in {
                          "Sampling", "Encoding", "Paused"}]
            if active:
                return False, [], (
                    f"{active[0].name} is currently "
                    f"{self.items[active[0]].status.lower()}")
            reserved = []
            for path in known:
                item = self.items.get(path)
                if item is not None and item.status in {
                        "Checking file...", "Waiting to scan", "Waiting", "Queued"}:
                    self.cleaning_reserved.add(path)
                    item.detail = "Temporarily reserved for Cleaner"
                    reserved.append(path)
            return True, reserved, ""

    def release_cleaning(self, paths: list[Path]) -> list[Path]:
        """Return Cleaner-reserved paths to their unchanged queue positions."""
        with self._lock:
            released = []
            for raw_path in paths:
                path = self._known_or_resolved(raw_path)
                if path not in self.cleaning_reserved:
                    continue
                self.cleaning_reserved.discard(path)
                item = self.items.get(path)
                if item is not None:
                    item.detail = ("Ready" if item.status == "Waiting" else item.detail)
                released.append(path)
            return released

    def set_force_encode(self, path: Path, enabled: bool) -> bool:
        """Set the per-file sampling override while the row is still waiting."""
        with self._lock:
            path = self._known_or_resolved(path)
            item = self.items.get(path)
            if item is None or item.status not in {
                    "Checking file...", "Waiting to scan", "Waiting", "Queued",
                    "Skipped", "Failed", "Cancelled", "Reverted"}:
                return False
            item.force_encode = enabled
            return True

    def set_selected(self, path: Path, selected: bool) -> bool:
        """Include or exclude a file that has not started processing."""
        with self._lock:
            path = self._known_or_resolved(path)
            item = self.items.get(path)
            if item is None or item.status not in {
                    "Checking file...", "Waiting to scan", "Waiting", "Not selected",
                    "Skipped", "Failed", "Cancelled", "Reverted"}:
                return False
            if selected == item.selected:
                return True
            item.selected = selected
            if not selected:
                item.status_before_excluded = item.status
                item.status = "Not selected"
                item.detail = "Excluded from processing"
                item.force_encode = False
            else:
                restored = item.status_before_excluded
                item.status = restored if restored in {
                    "Checking file...", "Waiting to scan", "Waiting"} else "Waiting"
                item.detail = ("Ready" if item.status == "Waiting"
                               else "Waiting for file to finish copying")
            return True

    def reorder(self, ordered_paths: list[Path]) -> None:
        """Apply the GUI order to ordinary waiting work.

        Reanalysis keeps its separate priority queue; active and finished rows
        are not made claimable by this operation.
        """
        with self._lock:
            resolved = [self._known_or_resolved(path) for path in ordered_paths]
            reordered: dict[Path, FileState] = {}
            for path in resolved:
                if path in self.items:
                    reordered[path] = self.items[path]
            for path, item in self.items.items():
                reordered.setdefault(path, item)
            self.items = reordered

    def queue_reanalysis(self, path: Path) -> bool:
        """Put a finished file ahead of ordinary waiting work."""
        with self._lock:
            path = self._known_or_resolved(path)
            item = self.items.get(path)
            if item is None or item.status not in {
                    "Skipped", "Failed", "Cancelled", "Reverted"}:
                return False
            self.completed.discard(path)
            item.status = "Queued"
            item.detail = "Queued for reanalysis"
            self.reanalysis_queue.append(path)
            return True

    def cancel_reanalysis(self, path: Path, status: str, detail: str) -> bool:
        """Cancel a queued (not yet claimed) reanalysis request."""
        with self._lock:
            path = self._known_or_resolved(path)
            item = self.items.get(path)
            if item is None or item.status != "Queued":
                return False
            self.reanalysis_queue = deque(
                queued for queued in self.reanalysis_queue if queued != path)
            item.status = status
            item.detail = detail
            self.completed.add(path)
            return True

    def has_pending(self) -> bool:
        with self._lock:
            return any(x.status in {"Waiting to scan", "Waiting", "Queued",
                                    "Checking file...",
                                    "Sampling", "Encoding"}
                       for x in self.items.values())

    def set_status(self, path: Path, status: str, detail: str | None = None) -> None:
        with self._lock:
            item = self.items[self._known_or_resolved(path)]
            item.status = status
            if detail is not None:
                item.detail = detail

    def mark_done(self, path: Path, status: str, detail: str = "") -> None:
        with self._lock:
            path = self._known_or_resolved(path)
            item = self.items[path]
            item.status = status
            item.detail = detail
            self.completed.add(path)

    def mark_reverted(self, path: Path) -> None:
        """Return a completed item to a safe, unselected restored state."""
        with self._lock:
            path = self._known_or_resolved(path)
            item = self.items[path]
            item.status = "Reverted"
            item.detail = "Original restored from backup"
            item.selected = False
            item.force_encode = False
            item.status_before_excluded = "Reverted"
            self.completed.discard(path)

    def mark_generated_output(self, path: Path) -> bool:
        """Prevent a newly written encode from being rediscovered this session.

        Returns True when the watcher had already inserted a separate row for
        the output, allowing the GUI to remove that transient duplicate.
        """
        with self._lock:
            path = self._known_or_resolved(path)
            self.completed.add(path)
            item = self.items.pop(path, None)
            self.reanalysis_queue = deque(
                queued for queued in self.reanalysis_queue if queued != path)
            return item is not None
