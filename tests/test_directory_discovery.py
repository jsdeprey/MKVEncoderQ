import inspect
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_optimizer.config import Config
from video_optimizer.gui import (
    DirectoryToggle, OptimizerGUI, _adaptive_rescan_interval,
    _background_folder_delay, _newly_missing_items,
)
from video_optimizer.queueing import LiveQueue
from video_optimizer.scanner import discover_inventory


class DirectoryDiscoveryTests(unittest.TestCase):
    def test_idle_inventory_reports_file_removed_since_previous_scan(self):
        removed = SimpleNamespace(path=Path("removed.mkv"), status="Missing")
        retained = SimpleNamespace(path=Path("kept.mkv"), status="Waiting")
        already_missing = SimpleNamespace(
            path=Path("old-missing.mkv"), status="Missing")
        previous = {
            removed.path: "Waiting",
            retained.path: "Waiting",
            already_missing.path: "Missing",
        }
        self.assertEqual(
            _newly_missing_items(previous, [removed, retained, already_missing]),
            [removed])

    def test_adaptive_rescan_interval_grows_with_large_libraries(self):
        self.assertEqual(_adaptive_rescan_interval(10, 100), 10)
        self.assertEqual(_adaptive_rescan_interval(10, 250), 60)
        self.assertEqual(_adaptive_rescan_interval(10, 1000), 300)
        self.assertEqual(_adaptive_rescan_interval(10, 3000), 900)
        self.assertEqual(_adaptive_rescan_interval(10, 100, 20), 80)

    def test_background_scan_throttles_only_when_useful(self):
        self.assertEqual(_background_folder_delay(100, True), 0)
        self.assertGreater(_background_folder_delay(1000, True),
                           _background_folder_delay(1000, False))
        self.assertGreater(_background_folder_delay(3000, True), 0)

    def test_incremental_discovery_during_encode_defers_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "arriving.mkv"
            path.write_bytes(b"still copying")
            live = LiveQueue(root, ready_seconds=10)
            live.refresh_incremental(readiness_enabled=False)
            self.assertEqual(live.items[path.resolve()].status, "Waiting to scan")
            self.assertIsNone(live.claim_waiting())
            live.refresh_incremental(readiness_enabled=True)
            self.assertEqual(live.items[path.resolve()].status, "Checking file...")
            self.assertIsNone(live.claim_waiting())

    def test_inventory_separates_filesystem_and_video_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "First"
            second = root / "Second"
            nested = second / "Artwork"
            first.mkdir()
            nested.mkdir(parents=True)
            (first / "movie.mkv").write_bytes(b"video")
            (first / "movie.srt").write_text("subtitle", encoding="utf-8")
            (second / "movie.mp4").write_bytes(b"video")
            (nested / "poster.jpg").write_bytes(b"image")
            progress = []
            metadata = []
            videos = list(discover_inventory(
                root, "_video_optimizer_backups",
                progress=lambda *values: progress.append(values),
                metadata=lambda *values: metadata.append(values)))
            self.assertEqual({path.name for path in videos},
                             {"movie.mkv", "movie.mp4"})
            self.assertEqual(progress[0], ("base", 1, 0, 2))
            self.assertEqual(progress[-1][1:], (4, 4, 2))
            self.assertEqual({values[0].name for values in metadata},
                             {"movie.mkv", "movie.mp4"})
            self.assertTrue(all(values[1] == 5 for values in metadata))

    def make_gui(self, root, enabled=True):
        switch = threading.Event()
        if enabled:
            switch.set()
        events = queue.Queue()
        table = Mock()
        table.get_children.return_value = ()
        inspection_gate = threading.Event()
        inspection_gate.set()
        return SimpleNamespace(
            after=Mock(), _idle_scan_tick=Mock(), running=False,
            scan_enabled=switch, idle_discovery=None, worker=None, watcher=None,
            input_var=SimpleNamespace(get=lambda: str(root)), live=None,
            table=table, scan_status_var=Mock(), _show_scan_status=Mock(),
            inventory_status_var=Mock(), inventory_progress={},
            inspection_inventory_gate=inspection_gate,
            _emit=lambda *event: events.put(event), events=events)

    def test_active_inspection_delays_directory_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            gui = self.make_gui(Path(directory))
            gui.inspection_thread = Mock()
            gui.inspection_thread.is_alive.return_value = True
            gui.inspection_cycle_active = True
            with patch("video_optimizer.gui.load_config", return_value=Config(
                    input_directory=directory, file_ready_seconds=0)):
                OptimizerGUI._idle_scan_tick(gui)
            self.assertIsNone(gui.live)
            self.assertTrue(gui.inspection_inventory_gate.is_set())

    def test_opening_discovers_without_sampling_or_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "video.mkv").write_bytes(b"test")
            gui = self.make_gui(root)
            config = Config(input_directory=str(root), file_ready_seconds=0)
            with patch("video_optimizer.gui.load_config", return_value=config), \
                    patch("video_optimizer.gui.scan_file") as sample, \
                    patch("video_optimizer.gui.encode") as encode:
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(timeout=5)
                self.assertFalse(gui.idle_discovery.is_alive())
                self.assertEqual(gui.live.snapshot()[0].status, "Waiting")
                event_names = []
                while not gui.events.empty():
                    event_names.append(gui.events.get_nowait()[0])
                self.assertIn("idle_discovery_batch", event_names)
                self.assertIn("idle_discovered", event_names)
                sample.assert_not_called()
                encode.assert_not_called()
                self.assertFalse(gui.running)

    def test_large_idle_discovery_reports_incremental_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(60):
                folder = root / f"folder-{index:03d}"
                folder.mkdir()
                (folder / f"video-{index:03d}.mkv").write_bytes(b"test")
            gui = self.make_gui(root)
            config = Config(input_directory=str(root), file_ready_seconds=0)
            with patch("video_optimizer.gui.load_config", return_value=config):
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(timeout=5)
            events = []
            while not gui.events.empty():
                events.append(gui.events.get_nowait())
            batches = [event for event in events
                       if event[0] == "idle_discovery_batch"]
            self.assertEqual([event[3] for event in batches], [25, 50, 60])
            self.assertEqual(events[-1][0], "idle_discovered")

    def test_off_performs_one_initial_inventory_without_rescanning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "video.mkv").write_bytes(b"test")
            gui = self.make_gui(root, enabled=False)
            config = Config(input_directory=str(root), file_ready_seconds=0)
            with patch("video_optimizer.gui.load_config", return_value=config):
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(timeout=5)
                self.assertEqual(len(gui.live.snapshot()), 1)
                first_thread = gui.idle_discovery
                OptimizerGUI._idle_scan_tick(gui)
                self.assertIs(gui.idle_discovery, first_thread)

    def test_unchanged_active_rescan_keeps_inventory_progress_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gui = self.make_gui(root, enabled=True)
            config = Config(
                input_directory=str(root), file_ready_seconds=0,
                rescan_interval_seconds=0)
            with patch("video_optimizer.gui.load_config", return_value=config):
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(timeout=5)
                while not gui.events.empty():
                    gui.events.get_nowait()
                gui.last_idle_scan_started = 0
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(timeout=5)
            events = []
            while not gui.events.empty():
                events.append(gui.events.get_nowait())
            self.assertFalse(any(
                event[0] == "idle_inventory_progress" for event in events))
            finished = [event for event in events
                        if event[0] == "idle_discovered"]
            self.assertEqual(len(finished), 1)
            self.assertTrue(finished[0][6])

    def test_silent_active_rescan_still_discovers_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gui = self.make_gui(root, enabled=True)
            config = Config(
                input_directory=str(root), file_ready_seconds=0,
                rescan_interval_seconds=0)
            with patch("video_optimizer.gui.load_config", return_value=config):
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(timeout=5)
                while not gui.events.empty():
                    gui.events.get_nowait()
                (root / "new-video.mkv").write_bytes(b"test")
                gui.last_idle_scan_started = 0
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(timeout=5)
            self.assertIn((root / "new-video.mkv").resolve(), gui.live.items)
            event_names = []
            while not gui.events.empty():
                event_names.append(gui.events.get_nowait()[0])
            self.assertIn("idle_discovery_batch", event_names)
            self.assertIn("idle_discovered", event_names)

    def test_processing_does_not_start_duplicate_idle_watcher(self):
        gui = self.make_gui(Path("unused"))
        gui.running = True
        with patch("video_optimizer.gui.load_config") as config:
            OptimizerGUI._idle_scan_tick(gui)
            config.assert_not_called()

    def test_start_with_scanning_off_reuses_inventory_without_refresh(self):
        switch = threading.Event()
        events = []
        live = Mock()
        live.snapshot.return_value = []
        live.last_discovered_count = 0
        gui = SimpleNamespace(
            live=live, scan_enabled=switch, scan_cycle_complete=threading.Event(),
            stop_event=threading.Event(), encode_active=threading.Event(),
            completed_outputs={}, _emit=lambda *event: events.append(event),
        )
        OptimizerGUI._watch(gui, Config(rescan_interval_seconds=5))
        live.refresh.assert_not_called()
        self.assertTrue(gui.scan_cycle_complete.is_set())
        self.assertEqual(events, [("scan_activity", False, None)])

    def test_active_start_waits_before_first_background_rescan(self):
        switch = threading.Event()
        switch.set()
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        live = Mock()
        live.snapshot.return_value = []
        live.last_discovered_count = 0
        gui = SimpleNamespace(
            live=live, scan_enabled=switch, scan_cycle_complete=threading.Event(),
            stop_event=stop, encode_active=threading.Event(),
            completed_outputs={}, _emit=Mock(),
        )
        OptimizerGUI._watch(gui, Config(rescan_interval_seconds=7))
        stop.wait.assert_called_once_with(7)
        live.refresh.assert_not_called()

    def test_scan_now_interrupts_scheduled_wait_without_changing_interval(self):
        manual = threading.Event()
        manual.set()
        gui = SimpleNamespace(
            scan_now_event=manual, stop_event=threading.Event(),
            encode_active=threading.Event(), _emit=Mock())
        self.assertFalse(OptimizerGUI._wait_for_scan_interval(gui, 900, 3497))
        gui._emit.assert_called_once_with("scan_schedule", 900, 3497, False)

    def test_active_rescan_does_not_emit_unchanged_rows(self):
        switch = threading.Event()
        switch.set()
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        item = SimpleNamespace(
            path=Path("movie.mkv"), status="Waiting", detail="Ready",
            size=1234, modified_ns=99)
        live = Mock()
        live.snapshot.return_value = [item]
        live.last_discovered_count = 1
        events = []
        gui = SimpleNamespace(
            live=live, scan_enabled=switch, scan_cycle_complete=threading.Event(),
            stop_event=stop, encode_active=threading.Event(),
            completed_outputs={}, _emit=lambda *event: events.append(event),
        )
        OptimizerGUI._watch(gui, Config(rescan_interval_seconds=3))
        live.refresh_incremental.assert_called_once()
        self.assertFalse(any(event[0] == "row" for event in events))

    def test_idle_scan_keeps_completed_outputs_and_manual_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("done.mkv", "a.mkv", "b.mkv"):
                (root / name).write_bytes(b"test")
            gui = self.make_gui(root)
            with patch("video_optimizer.gui.load_config", return_value=Config(
                    input_directory=str(root), file_ready_seconds=0)):
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(5)
                live = gui.live
                live.mark_done(root / "done.mkv", "Completed")
                live.reorder([root / "b.mkv", root / "a.mkv", root / "done.mkv"])
                OptimizerGUI._idle_scan_tick(gui)
                gui.idle_discovery.join(5)
                self.assertIs(gui.live, live)
                self.assertEqual(live.claim_waiting().path, root / "b.mkv")
                self.assertIn(root / "done.mkv", live.completed)

    def test_start_cancels_quiet_background_rescan_without_waiting(self):
        source = inspect.getsource(OptimizerGUI.start)
        self.assertIn("quiet_discovery", source)
        self.assertIn("self.directory_discovery_cancel.set()", source)
        self.assertIn("inventory_busy = discovery_alive and not quiet_discovery", source)

    def test_cancelled_incremental_scan_never_marks_unvisited_files_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "movie.mkv"
            path.write_bytes(b"test")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh_incremental()
            self.assertEqual(live.items[path.resolve()].status, "Waiting")

            with patch("video_optimizer.queueing.discover_inventory",
                       return_value=iter(())):
                live.refresh_incremental(cancelled=lambda: True)

            self.assertEqual(live.items[path.resolve()].status, "Waiting")

    def test_rename_during_incremental_inventory_is_not_marked_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = (root / "Old.mkv").resolve()
            new = (root / "New.mkv").resolve()
            old.write_bytes(b"test")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh_incremental()

            def stale_inventory(*_args, **_kwargs):
                yield old
                old.rename(new)
                live.rename_paths({old: new})

            with patch("video_optimizer.queueing.discover_inventory",
                       side_effect=stale_inventory):
                live.refresh_incremental()

            self.assertNotIn(old, live.items)
            self.assertIn(new, live.items)
            self.assertEqual(live.items[new].status, "Waiting")
            self.assertEqual(live.recently_renamed, set())

    def test_removed_waiting_file_becomes_missing_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "movie.mkv"
            path.write_bytes(b"test")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=1)
            self.assertEqual(live.items[path.resolve()].status, "Waiting")

            path.unlink()
            live.refresh(now=2)
            item = live.items[path.resolve()]
            self.assertEqual(item.status, "Missing")
            self.assertEqual(item.detail, "File is no longer in the scanned directory")
            self.assertIsNone(live.claim_waiting())

            path.write_bytes(b"returned")
            live.refresh(now=3)
            self.assertEqual(live.items[path.resolve()].status, "Waiting")

    def test_completed_file_becomes_missing_if_it_is_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "movie.mkv"
            path.write_bytes(b"test")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=1)
            live.mark_done(path, "Completed", "Finished")
            path.unlink()
            live.refresh(now=2)
            item = live.items[path.resolve()]
            self.assertEqual(item.status, "Missing")
            self.assertEqual(item.status_before_missing, "Completed")

    def test_switch_keyboard_and_mouse_use_same_action(self):
        switch = SimpleNamespace(enabled=False, focus_set=Mock(), command=Mock())
        self.assertEqual(DirectoryToggle._toggle(switch), "break")
        switch.command.assert_called_once_with(True)
