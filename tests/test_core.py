import json
import queue
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_optimizer.config import Config, create_default_config, load_config, save_config
from video_optimizer.benefit import analyze_benefit
from video_optimizer.cli import parser
from video_optimizer.encoder import (BackupAlreadyExists, EncodeCancelled,
                                     _format_progress,
                                     _run_handbrake_with_progress, build_command,
                                     encode, expected_handbrake_subtitle_count,
                                     verify)
from video_optimizer.models import Decision, MediaInfo, SampleResult, Stream
from video_optimizer.tools import resolve_program
from video_optimizer.pause import PauseController
from video_optimizer.probe import probe
from video_optimizer.queueing import FileState, LiveQueue
from video_optimizer.scanner import inspect_for_forced_encode
from video_optimizer.subprocesses import helper_creation_flags
from video_optimizer.gui import (OptimizerGUI, _can_clean, _can_force, _can_reanalyze, _can_reorder,
                                 _can_select, _encode_text,
                                 _encode_all_text,
                                 _completed_progress, _duration_text,
                                 _force_all_text, _force_text,
                                 _idle_row_needs_update,
                                 _inventory_decision,
                                 _preserve_completed_refresh,
                                 _progressive_action_text,
                                 _reanalyze_action_text, _row_action_text,
                                 _sampling_filename_text,
                                 _scan_status_text, _show_reanalyze,
                                 _should_queue_row_inspection,
                                 _size_text)


def media(codec="h264", bitrate=10_000_000, height=1080, audio="aac"):
    video = Stream(0, "video", codec, width=1920, height=height, bit_rate=bitrate)
    sound = Stream(1, "audio", audio, channels=6, channel_layout="5.1")
    return MediaInfo(Path("movie.mp4"), 100.0, bitrate, 1000, (video, sound))


class CoreTests(unittest.TestCase):
    def test_inventory_rows_cannot_start_inspection(self):
        self.assertFalse(_should_queue_row_inspection(True, False, True))
        self.assertTrue(_should_queue_row_inspection(True, False, False))
        self.assertFalse(_should_queue_row_inspection(True, True, False))

    def test_successful_inspection_keeps_unselected_row_excluded(self):
        path = Path("movie.mkv")
        values = ["☰", "☐ Force", path.name, "Not selected", "—", "—",
                  "1.0 GiB", "—", "Excluded from processing", "—",
                  "☐ Encode", ".", "☐ Clean", "—", "—"]
        table = Mock()
        table.exists.return_value = True
        table.item.return_value = values
        result = SimpleNamespace(media_text="H.264 • 1080p", rating="WASTEFUL")
        gui = SimpleNamespace(live=None, table=table)
        OptimizerGUI._update_row_media(gui, path, result)
        updated = table.item.call_args.kwargs["values"]
        self.assertEqual(updated[8], "Excluded from processing")
        self.assertEqual(updated[13], "H.264 • 1080p")
        self.assertEqual(updated[14], "WASTEFUL")

    def test_late_inspection_does_not_erase_skipped_reason(self):
        path = Path("movie.mkv")
        values = ["☰", "☐ Force", path.name, "Skipped", "—", "7.8%",
                  "4.2 GiB", "3.8 GiB", "Skip — below 10.0% limit", "—",
                  "☑ Encode", ".", "☐ Clean", "—", "—"]
        table = Mock()
        table.exists.return_value = True
        table.item.return_value = values
        result = SimpleNamespace(media_text="H.264 • 1080p", rating="WASTEFUL")
        gui = SimpleNamespace(live=None, table=table)

        OptimizerGUI._update_row_media(gui, path, result)

        updated = table.item.call_args.kwargs["values"]
        self.assertEqual(updated[8], "Skip — below 10.0% limit")
        self.assertEqual(updated[13], "H.264 • 1080p")
        self.assertEqual(updated[14], "WASTEFUL")

    def test_inventory_refresh_preserves_terminal_reason(self):
        for status in ("Skipped", "Failed", "Cancelled", "Reverted"):
            item = SimpleNamespace(status=status, detail=f"{status} reason")
            self.assertEqual(
                _inventory_decision(item, inspected=True, inspect_enabled=True),
                f"{status} reason",
            )

    def test_selecting_inspected_file_restores_inspected_ready_result(self):
        path = Path("movie.mkv")
        item = FileState(path, status="Not selected", selected=False,
                         detail="Excluded from processing")
        values = ["☰", "☐ Force", path.name, "Not selected", "—", "—",
                  "1.0 GiB", "—", "Excluded from processing", "—",
                  "☐ Encode", ".", "☐ Clean", "H.264 • 1080p", "WASTEFUL"]
        table = Mock()
        table.item.return_value = values
        table.get_children.return_value = ()
        live = LiveQueue(Path("."), ready_seconds=0)
        live.items[path] = item
        gui = SimpleNamespace(
            live=live, table=table, file_selection={}, force_encode_paths=set(),
            inspection_results={path: object()}, inspection_errors={},
            _refresh_encode_all_heading=Mock(), _refresh_force_all_heading=Mock())
        self.assertTrue(OptimizerGUI._set_file_selected(gui, path, True, values))
        updated = table.item.call_args.kwargs["values"]
        self.assertEqual(updated[8], "Media inspected — Ready")

    def test_idle_discovery_skips_unchanged_visible_rows(self):
        item = FileState(Path("movie.mkv"), status="Waiting", size=1024)
        values = ["☰", "☐ Force", "movie.mkv", "Waiting", "—", "—",
                  "1.0 KiB", "—", "Awaiting Start", "—", "☑ Encode",
                  ".", "☐ Clean"]
        self.assertFalse(_idle_row_needs_update(values, item))
        item.status = "Missing"
        item.detail = "File is no longer in the scanned directory"
        self.assertTrue(_idle_row_needs_update(values, item))

    def test_row_event_heading_refresh_is_batched(self):
        paths = [Path(f"movie-{index}.mkv").resolve() for index in range(100)]
        events = queue.Queue()
        for path in paths:
            events.put(("row", path, "Waiting", "—", "—", "1.0 KiB", "—",
                        "Awaiting Start"))
        table = Mock()
        table.exists.return_value = False
        gui = SimpleNamespace(
            events=events, table=table, queued_reanalysis={}, queued_force={},
            reanalyze_target=None, missing_row_values={}, file_selection={},
            live=SimpleNamespace(items={path: SimpleNamespace(selected=True)
                                       for path in paths}),
            default_encode_selected=True, force_encode_paths=set(), clean_paths=set(),
            clean_available=True, status_callback=None,
            inspection_results={}, inspect_new_files=False,
            _refresh_encode_all_heading=Mock(), _refresh_force_all_heading=Mock(),
            _refresh_clean_all_heading=Mock(), _refresh_missing_button=Mock(),
            after=Mock(), _drain_events=Mock(),
            global_refresh_pending=False, missing_refresh_pending=False,
            last_global_refresh=time.monotonic(),
        )
        while not events.empty():
            OptimizerGUI._drain_events(gui)
        self.assertEqual(table.insert.call_count, 100)
        gui._refresh_encode_all_heading.assert_called_once_with()
        gui._refresh_force_all_heading.assert_called_once_with()
        gui._refresh_clean_all_heading.assert_called_once_with()
        gui._refresh_missing_button.assert_called_once_with()

    def test_active_encode_feedback_uses_priority_queue(self):
        gui = SimpleNamespace(events=queue.Queue(), urgent_events=queue.Queue())
        OptimizerGUI._emit(gui, "log", "ordinary")
        OptimizerGUI._emit(gui, "details", "Movie.mkv", "RF 22", "Projected")
        OptimizerGUI._emit(
            gui, "row", Path("Movie.mkv"), "Encoding", "1.0%", "—",
            "1 GiB", "—", "Encoding")

        self.assertEqual(gui.events.get_nowait()[0], "log")
        self.assertEqual(gui.urgent_events.get_nowait()[0], "details")
        self.assertEqual(gui.urgent_events.get_nowait()[0], "row")

    def test_bad_display_event_does_not_stop_event_pump(self):
        events = queue.Queue()
        events.put(("row",))
        gui = SimpleNamespace(
            events=events, after=Mock(), _drain_events=Mock(),
            _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock(),
            _refresh_clean_all_heading=Mock(),
            _refresh_missing_button=Mock())

        OptimizerGUI._drain_events(gui)

        gui.after.assert_called_once_with(100, gui._drain_events)

    def test_folder_change_discards_both_old_session_event_queues(self):
        events = queue.Queue()
        urgent = queue.Queue()
        events.put(("row", Path("old.mkv"), "Waiting"))
        urgent.put(("details", "old.mkv", "", ""))
        gui = SimpleNamespace(events=events, urgent_events=urgent)

        OptimizerGUI._discard_retired_session_events(gui)

        self.assertTrue(events.empty())
        self.assertTrue(urgent.empty())

    def test_repeated_encode_progress_never_resolves_known_nas_path(self):
        path = Path("//nas/share/movies/movie.mkv")
        events = queue.Queue()
        for percent in range(250):
            events.put(("row", path, "Encoding", f"{percent / 2:.1f}%",
                        "—", "25.0 GiB", "—", "Encoding with HandBrake"))
        values = ["☰", "☐ Force", path.name, "Encoding", "0.0%", "—",
                  "25.0 GiB", "—", "Encoding with HandBrake", "—",
                  "☑ Encode", str(path.parent), "☐ Clean", "H.264 • 1080p",
                  "WASTEFUL", "Present"]
        table = Mock()
        table.exists.return_value = True
        table.set.return_value = "Encoding"
        table.item.return_value = values
        item = FileState(path, status="Encoding", selected=True)
        gui = SimpleNamespace(
            events=events, table=table, queued_reanalysis={}, queued_force={},
            reanalyze_target=None, missing_row_values={}, file_selection={path: True},
            live=SimpleNamespace(items={path: item}), default_encode_selected=True,
            force_encode_paths=set(), clean_paths=set(), clean_available=True,
            status_callback=None, inspection_results={}, inspect_new_files=False,
            directory_discovery_active=False, _backup_status=Mock(return_value="Present"),
            queue_inspection=Mock(), _refresh_encode_all_heading=Mock(),
            _refresh_force_all_heading=Mock(), _refresh_clean_all_heading=Mock(),
            _refresh_missing_button=Mock(), after=Mock(), _drain_events=Mock(),
        )
        with patch.object(
                Path, "resolve",
                side_effect=AssertionError("progress performed a NAS lookup")):
            OptimizerGUI._drain_events(gui)
        self.assertGreater(table.item.call_count, 0)
        gui._backup_status.assert_called()
        self.assertTrue(all(
            call.kwargs.get("check_disk") is False
            for call in gui._backup_status.call_args_list))
        gui._refresh_encode_all_heading.assert_not_called()
        gui._refresh_force_all_heading.assert_not_called()
        gui._refresh_clean_all_heading.assert_not_called()

    def test_existing_backup_is_an_expected_skip_condition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"encoded source")
            backup = root / "movie.mkv.bak"
            backup.write_bytes(b"original source")
            item = media()
            item = MediaInfo(movie, item.duration, item.bit_rate,
                             movie.stat().st_size, item.streams)

            with self.assertRaises(BackupAlreadyExists) as raised:
                encode(item, root, root, Config(), True)

            self.assertEqual(raised.exception.backup_path, backup)

    def test_cancelled_encode_removes_partial_temporary_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"source")
            item = media()
            item = MediaInfo(movie, item.duration, item.bit_rate,
                             movie.stat().st_size, item.streams)

            def cancelled(*args, **kwargs):
                Path(args[3]).write_bytes(b"partial output")
                raise EncodeCancelled("encode cancelled by user")

            with patch("video_optimizer.encoder._run_handbrake_with_progress",
                       side_effect=cancelled):
                with self.assertRaises(EncodeCancelled):
                    encode(item, root, root, Config(), False)

            self.assertTrue(movie.exists())
            self.assertEqual(list(root.glob(".video-optimizer-*.mkv")), [])

    def test_file_details_reports_retained_failed_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = (root / "movie.mkv").resolve()
            movie.write_bytes(b"source")
            retained = root / ".video-optimizer-failed.mkv"
            retained.write_bytes(b"complete encoded output")
            values = ["☰", "☐ Force", movie.name, "Failed", "—", "—",
                      "6.0 GiB", "2.0 GiB",
                      "verification failed: subtitle stream count changed",
                      "[ Reanalyze ]", "☑ Encode", str(movie.parent), "—",
                      "—", "—", "Not created"]
            gui = SimpleNamespace(
                force_encode_paths=set(), completed_outputs={},
                retained_failed_outputs={movie: retained},
                encode_failure_details={
                    movie: "verification failed: subtitle stream count changed"},
                inspection_results={}, inspection_errors={})

            details = OptimizerGUI._file_details_text(gui, movie, values)

            self.assertIn("Encoding failure details:", details)
            self.assertIn(str(retained), details)
            self.assertIn("Retained file exists: Yes", details)
            self.assertIn("Retained file size:", details)

    def test_ffprobe_disconnects_inherited_console_input(self):
        payload = json.dumps({
            "format": {"duration": "100.0", "size": "1000"},
            "streams": [{"index": 0, "codec_type": "video",
                         "codec_name": "h264", "width": 1920, "height": 1080}],
        })
        completed = type("Result", (), {
            "returncode": 0, "stdout": payload, "stderr": "",
        })()
        with tempfile.TemporaryDirectory() as directory, \
                patch("video_optimizer.probe.subprocess.run", return_value=completed) as run:
            movie = Path(directory) / "movie.mkv"
            movie.write_bytes(b"source")
            probe(movie, "ffprobe")
        self.assertIs(run.call_args.kwargs["stdin"], __import__("subprocess").DEVNULL)

    def test_gui_result_formatting(self):
        self.assertEqual(_size_text(5 * 1024 ** 3), "5.0 GiB")
        self.assertEqual(_duration_text(2325), "0:38:45")

    def test_per_file_reanalyze_action_is_available_only_when_safe(self):
        self.assertTrue(_can_reanalyze("Skipped", running=False))
        self.assertTrue(_can_reanalyze("Failed", running=False))
        self.assertFalse(_can_reanalyze("Completed", running=False))
        self.assertFalse(_can_reanalyze("Encoding", running=False))
        self.assertTrue(_can_reanalyze("Skipped", running=True))
        self.assertTrue(_can_reanalyze("Queued", running=True))

    def test_reanalyze_button_remains_visible_for_finished_rows(self):
        self.assertTrue(_show_reanalyze("Skipped"))
        self.assertTrue(_show_reanalyze("Failed"))
        self.assertTrue(_show_reanalyze("Cancelled"))
        self.assertFalse(_show_reanalyze("Completed"))
        self.assertFalse(_show_reanalyze("Sampling"))
        self.assertFalse(_show_reanalyze("Waiting"))
        self.assertFalse(_show_reanalyze("Missing"))

    def test_reanalyze_action_is_visually_button_like(self):
        self.assertEqual(_reanalyze_action_text(), "[ Reanalyze ]")
        self.assertEqual(
            _reanalyze_action_text(active=True), "[ Reanalyzing... ]")
        self.assertEqual(
            _reanalyze_action_text(queued=True), "[ Queued ]")

    def test_progressive_reanalysis_uses_direct_action_labels(self):
        self.assertEqual(_progressive_action_text(3, 6), "[ Reanalyze ]")
        self.assertEqual(
            _progressive_action_text(3, 6, queued=True), "[ Queued ]")
        self.assertEqual(
            _progressive_action_text(3, 6, active=True), "[ Reanalyzing... ]")
        self.assertEqual(_progressive_action_text(6, 6), "[ Maximum 6/6 ]")

    def test_action_column_distinguishes_active_and_completed_rows(self):
        self.assertEqual(_row_action_text("Sampling"), "[ Sampling... ]")
        self.assertEqual(_row_action_text("Encoding"), "[ Encoding... ]")
        self.assertEqual(_row_action_text("Paused"), "[ Paused ]")
        self.assertEqual(_row_action_text("Completed"), "[ Play ]")
        self.assertEqual(_row_action_text("Waiting"), "—")
        self.assertEqual(_row_action_text("Skipped"), "[ Reanalyze ]")
        self.assertEqual(_row_action_text("Queued", reanalysis_queued=True),
                         "[ Queued ]")

    def test_force_encode_checkbox_and_safe_states(self):
        self.assertEqual(_force_text(False), "☐ Force")
        self.assertEqual(_force_text(True), "☑ Force")
        self.assertTrue(_can_force("Waiting"))
        self.assertTrue(_can_force("Skipped"))
        self.assertTrue(_can_force("Reverted"))
        self.assertFalse(_can_force("Encoding"))
        self.assertFalse(_can_force("Completed"))
        self.assertFalse(_can_force("Missing"))
        self.assertTrue(_can_reorder("Waiting"))
        self.assertFalse(_can_reorder("Completed"))
        self.assertFalse(_can_reorder("Missing"))
        self.assertFalse(_can_clean("Missing"))
        self.assertTrue(_can_clean("Completed"))
        self.assertEqual(_force_all_text([True, True]), "☑ Force All")
        self.assertEqual(_force_all_text([False, False]), "☐ Force All")
        self.assertEqual(_force_all_text([True, False]), "◩ Force All")

    def test_encode_selection_checkbox_and_safe_states(self):
        self.assertEqual(_encode_text(True), "☑ Encode")
        self.assertEqual(_encode_text(False), "☐ Encode")
        self.assertTrue(_can_select("Waiting"))
        self.assertTrue(_can_select("Not selected"))
        self.assertTrue(_can_select("Reverted"))
        self.assertFalse(_can_select("Sampling"))
        self.assertFalse(_can_select("Completed"))
        self.assertFalse(_can_select("Missing"))
        self.assertEqual(_encode_all_text([True, True]), "☑ Encode All")
        self.assertEqual(_encode_all_text([False, False]), "☐ Encode All")
        self.assertEqual(_encode_all_text([True, False]), "◩ Encode All")

    def test_new_files_are_selected_by_default(self):
        self.assertTrue(Config().select_new_files_for_encoding)

    def test_fresh_install_inspects_files_by_default(self):
        self.assertTrue(Config().inspect_new_files)

    def test_fresh_install_defaults_to_first_english_and_dark(self):
        config = Config()
        self.assertEqual(config.audio_stream_mode, "first_english")
        self.assertEqual(config.theme, "dark")
        self.assertTrue(config.mark_filenames_with_spaces)
        self.assertTrue(config.mark_filenames_missing_year_or_episode)

    def test_new_file_selection_default_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            save_config(replace(Config(), select_new_files_for_encoding=False), config_path)
            self.assertFalse(load_config(config_path).select_new_files_for_encoding)

    def test_unselected_new_file_is_visible_but_not_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"movie")
            live = LiveQueue(root, ready_seconds=0, default_selected=False)
            live.refresh(now=0)
            item = live.items[movie.resolve()]
            self.assertEqual(item.status, "Not selected")
            self.assertFalse(item.selected)
            self.assertIsNone(live.claim_waiting())
            self.assertFalse(live.has_pending())
            self.assertTrue(live.set_selected(movie, True))
            live.refresh(now=1)
            self.assertEqual(live.claim_waiting().path, movie.resolve())

    def test_known_queue_path_does_not_resolve_unc_path_again(self):
        path = Path("already-canonical/movie.mkv")
        live = LiveQueue(Path("already-canonical"), ready_seconds=0)
        live.items[path] = FileState(
            path, status="Waiting", selected=True, detail="Ready")
        with patch.object(Path, "resolve",
                          side_effect=AssertionError("unexpected filesystem lookup")):
            self.assertTrue(live.set_selected(path, False))
            live.reorder([path])
        self.assertEqual(live.items[path].status, "Not selected")

    def test_force_all_uses_3500_known_paths_without_nas_lookups(self):
        paths = [Path(f"//nas/share/folder-{index}/movie.mkv")
                 for index in range(3_500)]
        live = LiveQueue(Path("//nas/share"), ready_seconds=0)
        for path in paths:
            live.items[path] = FileState(path, status="Waiting", selected=True)
        values = ["☰", "☐ Force", "movie.mkv", "Waiting", "—", "—",
                  "1.0 GiB", "—", "Ready", "—", "☑ Encode", "//nas/share",
                  "☐ Clean", "H.264 • 1080p", "WASTEFUL", "—"]
        table = Mock()
        table.item.return_value = values
        table.get_children.return_value = ()
        gui = SimpleNamespace(
            live=live, table=table, force_encode_paths=set(), queued_force={},
            _all_table_rows=lambda: [str(path) for path in paths],
            _set_file_forced=Mock(return_value=True),
            _refresh_force_all_heading=Mock(),
        )
        with patch.object(
                Path, "resolve",
                side_effect=AssertionError("Force All performed a NAS lookup")):
            OptimizerGUI.toggle_all_force_encode(gui)
        self.assertEqual(gui._set_file_forced.call_count, 0)
        self.assertTrue(all(item.force_encode for item in live.snapshot()))
        gui._refresh_force_all_heading.assert_called_once_with()

    def test_3500_file_inspection_handoff_uses_known_paths_only(self):
        paths = [Path(f"//nas/share/folder-{index}/movie.mkv")
                 for index in range(3_500)]
        live = LiveQueue(Path("//nas/share"), ready_seconds=0)
        for path in paths:
            live.items[path] = FileState(path, status="Waiting", selected=True)
        worker = Mock()
        worker.is_alive.return_value = True
        gui = SimpleNamespace(
            live=live, inspection_results={}, inspection_errors={},
            inspection_lock=threading.Lock(), inspection_enqueued=set(),
            inspection_total=0, inspection_queue=queue.Queue(),
            inspection_started_at=0.0, inspection_cancel=threading.Event(),
            cancel_inspection_button=Mock(), inspection_thread=worker,
            inspection_cycle_active=True,
        )
        with patch.object(
                Path, "resolve",
                side_effect=AssertionError("inspection handoff performed a NAS lookup")):
            queued = OptimizerGUI.queue_inspection(gui, paths)
        self.assertEqual(queued, 3_500)
        self.assertEqual(gui.inspection_queue.qsize(), 3_500)
        self.assertEqual(len(gui.inspection_enqueued), 3_500)

    def test_unchecking_file_clears_force_encode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"movie")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=0)
            self.assertTrue(live.set_force_encode(movie, True))
            self.assertTrue(live.set_selected(movie, False))
            item = live.items[movie.resolve()]
            self.assertFalse(item.force_encode)
            self.assertEqual(item.status, "Not selected")

    def test_forced_encode_probes_without_running_samples(self):
        source = media()
        with patch("video_optimizer.scanner.probe", return_value=source) as mocked_probe:
            result = inspect_for_forced_encode(source.path, Config(), "ffprobe")
        mocked_probe.assert_called_once()
        self.assertEqual(result.decision, Decision.RECOMMEND)
        self.assertEqual(result.samples_tested, 0)
        self.assertEqual(result.sample_results, ())
        self.assertIn("sampling skipped", result.reason.lower())

    def test_sampling_results_identify_the_file(self):
        self.assertEqual(
            _sampling_filename_text(Path("Movies") / "Example Movie.mkv"),
            "File: Example Movie.mkv",
        )

    def test_active_scanning_status_is_unambiguous(self):
        self.assertEqual(_scan_status_text(False), "")
        self.assertEqual(_scan_status_text(False, scanning=True), "")
        self.assertEqual(_scan_status_text(False, last_scan="2:15:03 PM"), "")
        self.assertIn("press start",
                      _scan_status_text(True, session_running=False).lower())
        self.assertIn(
            "last scan 2:15:03 pm",
            _scan_status_text(
                True, last_scan="2:15:03 PM", session_running=False).lower())
        self.assertIn("scanning selected folder now",
                      _scan_status_text(True, True).lower())
        self.assertEqual(_scan_status_text(True, last_scan="2:15:03 PM"),
                         "● Last scan 2:15:03 PM")

    def test_active_scanning_status_explains_adaptive_mode_and_schedule(self):
        normal = _scan_status_text(
            True, item_count=100, next_scan_seconds=5)
        self.assertIn("Normal mode", normal)
        self.assertIn("Next scan in ~5 sec", normal)
        large = _scan_status_text(
            True, last_scan="5:20 PM", item_count=3497,
            next_scan_seconds=601, encoding=True)
        self.assertIn("Large-library mode", large)
        self.assertIn("Next scan in ~11 min", large)
        self.assertIn("throttled during encoding", large)
        self.assertIn("Last scan 5:20 PM", large)

    def test_gui_native_pause_uses_console_key_injection(self):
        class FakePopen:
            pid = 456
            stdin = None
            @staticmethod
            def poll():
                return None
        controller = PauseController(FakePopen(), "movie.mkv")
        controller.native_console = True
        with patch("video_optimizer.pause.send_console_key", return_value=True) as send:
            self.assertTrue(controller.request_pause())
            self.assertTrue(controller.request_resume())
        self.assertEqual([call.args[1] for call in send.call_args_list], ["p", "r"])

    def test_handbrake_launch_does_not_inherit_invalid_console_input(self):
        class FakePopen:
            pid = 456
            stdin = None
            stdout = ()

            @staticmethod
            def poll():
                return 0

            @staticmethod
            def wait(timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as directory, \
                patch("video_optimizer.encoder.subprocess.Popen",
                      return_value=FakePopen()) as popen:
            _run_handbrake_with_progress(
                ["HandBrakeCLI", "-i", "movie.mkv"], 100.0, "movie.mkv",
                Path(directory) / "output.mkv",
                pause_event=__import__("threading").Event(),
            )

        self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_windowed_helpers_do_not_open_console_windows(self):
        with patch("video_optimizer.subprocesses.os.name", "nt"), \
                patch("video_optimizer.subprocesses.sys.stdout", None):
            self.assertTrue(helper_creation_flags() & 0x08000000)

    def test_live_queue_waits_for_stable_file_and_deduplicates_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"first")
            live = LiveQueue(root, ready_seconds=10)
            self.assertEqual(live.refresh(now=0), [])
            self.assertEqual(live.items[movie.resolve()].status, "Checking file...")
            self.assertEqual([x.path for x in live.refresh(now=10)], [movie.resolve()])
            live.mark_done(movie, "Completed")
            self.assertEqual(live.refresh(now=30), [])

    def test_dismissed_file_stays_out_of_queue_for_current_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"movie")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=0)
            self.assertIn(movie.resolve(), live.items)

            live.dismiss(movie)
            live.refresh(now=1)

            self.assertNotIn(movie.resolve(), live.items)
            self.assertIn(movie.resolve(), live.completed)
            self.assertIsNone(live.claim_waiting())

    def test_generated_output_is_not_rediscovered_without_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mp4"
            output = root / "movie.mkv"
            source.write_bytes(b"source")
            live = LiveQueue(root, ready_seconds=0)
            self.assertEqual([item.path for item in live.refresh(now=0)],
                             [source.resolve()])
            live.mark_done(source, "Completed")
            source.unlink()
            output.write_bytes(b"encoded")
            self.assertFalse(live.mark_generated_output(output))
            self.assertEqual(live.refresh(now=1), [])

    def test_generated_output_removes_watcher_race_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "movie.mp4"
            output = root / "movie.mkv"
            source.write_bytes(b"source")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=0)
            live.mark_done(source, "Completed")
            source.unlink()
            output.write_bytes(b"encoded")
            live.refresh(now=1)
            self.assertTrue(live.mark_generated_output(output))
            self.assertNotIn(output.resolve(), live.items)
            self.assertEqual(live.refresh(now=2), [])

    def test_live_queue_resets_readiness_when_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"first")
            live = LiveQueue(root, ready_seconds=10)
            live.refresh(now=0)
            movie.write_bytes(b"changed-size")
            self.assertEqual(live.refresh(now=9), [])
            self.assertEqual(live.refresh(now=18), [])
            self.assertEqual(len(live.refresh(now=19)), 1)

    def test_default_encoder_preset_is_fast(self):
        self.assertEqual(Config().encoder_preset, "fast")

    def test_live_queue_discovers_nested_file_while_another_is_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mkv"
            first.write_bytes(b"first")
            live = LiveQueue(root, ready_seconds=10)
            live.refresh(now=0)
            live.refresh(now=10)
            self.assertIsNotNone(live.claim_waiting())
            live.set_status(first, "Encoding")
            nested = root / "incoming" / "second.mkv"
            nested.parent.mkdir()
            nested.write_bytes(b"second")
            live.refresh(now=15, readiness_enabled=False)
            self.assertEqual(live.items[first.resolve()].status, "Encoding")
            self.assertEqual(live.items[nested.resolve()].status, "Waiting to scan")
            self.assertEqual(live.claim_waiting(), None)
            live.refresh(now=16, readiness_enabled=True)
            self.assertEqual(live.items[nested.resolve()].status, "Checking file...")
            self.assertEqual(live.refresh(now=25, readiness_enabled=True), [])
            self.assertEqual([x.path for x in live.refresh(now=26, readiness_enabled=True)],
                             [nested.resolve()])

    def test_one_time_scan_finishes_known_files_without_discovering_new_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mkv"
            first.write_bytes(b"first")
            live = LiveQueue(root, ready_seconds=10)
            live.refresh(now=0)
            later = root / "later.mkv"
            later.write_bytes(b"later")
            ready = live.refresh(now=10, discover_new=False)
            self.assertEqual([item.path for item in ready], [first.resolve()])
            self.assertNotIn(later.resolve(), live.items)

    def test_live_queue_can_limit_a_reanalysis_to_one_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "selected.mkv"
            other = root / "other.mkv"
            selected.write_bytes(b"selected")
            other.write_bytes(b"other")
            live = LiveQueue(root, ready_seconds=0,
                             allowed_paths={selected})
            ready = live.refresh(now=0)
            self.assertEqual([item.path for item in ready], [selected.resolve()])
            self.assertNotIn(other.resolve(), live.items)

    def test_multiple_reanalyses_are_claimed_in_click_order_and_can_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ("first.mkv", "second.mkv", "third.mkv")]
            for path in paths:
                path.write_bytes(path.name.encode())
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=0)
            for path in paths:
                live.mark_done(path, "Skipped", "Below threshold")
            self.assertTrue(live.queue_reanalysis(paths[1]))
            self.assertTrue(live.queue_reanalysis(paths[0]))
            self.assertTrue(live.queue_reanalysis(paths[2]))
            self.assertTrue(
                live.cancel_reanalysis(paths[0], "Skipped", "Below threshold"))
            self.assertEqual(live.claim_waiting().path, paths[1].resolve())
            self.assertEqual(live.claim_waiting().path, paths[2].resolve())

    def test_waiting_queue_follows_drag_reorder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ("first.mkv", "second.mkv", "third.mkv")]
            for path in paths:
                path.write_bytes(path.name.encode())
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=0)
            live.reorder([paths[2], paths[0], paths[1]])
            self.assertEqual(live.claim_waiting().path, paths[2].resolve())
            self.assertEqual(live.claim_waiting().path, paths[0].resolve())
            self.assertEqual(live.claim_waiting().path, paths[1].resolve())

    def test_gui_drag_order_replaces_stale_sort_order_and_preserves_hidden_rows(self):
        first = str(Path("/movies/first.mkv"))
        hidden = str(Path("/movies/hidden.mkv"))
        second = str(Path("/movies/second.mkv"))
        live = Mock()
        gui = SimpleNamespace(
            live=live,
            table=SimpleNamespace(get_children=lambda: (second, first)),
            filter_order=[first, hidden, second])

        OptimizerGUI._sync_queue_order(gui)

        self.assertEqual(gui.filter_order, [second, hidden, first])
        live.reorder.assert_called_once_with(
            [Path(second), Path(hidden), Path(first)])

    def test_cleaner_reservation_skips_waiting_file_then_restores_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mkv"
            second = root / "second.mkv"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=0)

            allowed, reserved, reason = live.reserve_for_cleaning([first])
            self.assertTrue(allowed)
            self.assertEqual(reason, "")
            self.assertEqual(reserved, [first.resolve()])
            self.assertEqual(live.claim_waiting().path, second.resolve())
            live.release_cleaning([first])
            self.assertEqual(live.claim_waiting().path, first.resolve())

    def test_cleaner_reservation_rejects_active_encode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"movie")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=0)
            active = live.claim_waiting()
            live.set_status(active.path, "Encoding")

            allowed, reserved, reason = live.reserve_for_cleaning([movie])

            self.assertFalse(allowed)
            self.assertEqual(reserved, [])
            self.assertIn("currently encoding", reason)

    def test_skipped_file_can_be_forced_and_claimed_without_analysis_queue_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "skipped.mkv"
            movie.write_bytes(b"movie")
            live = LiveQueue(root, ready_seconds=0)
            live.refresh(now=0)
            live.mark_done(movie, "Skipped", "Below threshold")
            self.assertTrue(live.set_force_encode(movie, True))
            self.assertTrue(live.queue_reanalysis(movie))
            claimed = live.claim_waiting()
            self.assertEqual(claimed.path, movie.resolve())
            self.assertTrue(claimed.force_encode)

    def test_default_projected_savings_threshold_is_ten_percent(self):
        self.assertEqual(Config().minimum_projected_savings_percent, 10.0)

    def test_sample_projection_recommends_sufficient_savings(self):
        source = media()
        source = MediaInfo(source.path, 100.0, source.bit_rate, 1_000_000,
                           source.streams)
        def fake_run(command, check=False, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"x" * 100_000)
            return type("Result", (), {"returncode": 0})()
        with patch("video_optimizer.benefit.subprocess.run", side_effect=fake_run):
            result = analyze_benefit(source, Config(sample_duration_seconds=20),
                                     "HandBrakeCLI")
        self.assertEqual(result.decision, Decision.RECOMMEND)
        self.assertAlmostEqual(result.projected_savings_percent, 50.0)
        self.assertEqual(result.samples_tested, 3)
        self.assertEqual(len(result.sample_results), 3)
        self.assertEqual([sample.number for sample in result.sample_results], [1, 2, 3])
        self.assertTrue(all(sample.projected_savings_percent == 50.0
                            for sample in result.sample_results))
        self.assertIsNotNone(result.sampling_elapsed_seconds)

    def test_six_sample_results_preserve_each_projection(self):
        source = media()
        source = MediaInfo(source.path, 120.0, source.bit_rate, 1_200_000,
                           source.streams)
        sizes = iter((50_000, 60_000, 70_000, 80_000, 90_000, 100_000))

        def fake_run(command, check=False, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(
                b"x" * next(sizes))
            return type("Result", (), {"returncode": 0})()

        config = Config(sample_count=6, sample_duration_seconds=10)
        with patch("video_optimizer.benefit.subprocess.run", side_effect=fake_run):
            result = analyze_benefit(source, config, "HandBrakeCLI")

        self.assertEqual(len(result.sample_results), 6)
        self.assertEqual([sample.total for sample in result.sample_results], [6] * 6)
        self.assertEqual(result.projected_size, 900_000)
        self.assertAlmostEqual(result.projected_savings_percent, 25.0)

    def test_reanalysis_variants_use_new_times_in_every_section(self):
        from video_optimizer.benefit import _sample_starts

        first, length = _sample_starts(600, 3, 10, variant=0)
        second, _ = _sample_starts(600, 3, 10, variant=1)
        third, _ = _sample_starts(600, 3, 10, variant=2)

        self.assertEqual(first, [95.0, 295.0, 495.0])
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)
        for index, starts in enumerate(zip(first, second, third)):
            section_start = index * 200
            section_end = section_start + 200
            self.assertEqual(len(set(starts)), 3)
            self.assertTrue(all(section_start <= start
                                and start + length <= section_end
                                for start in starts))

    def test_short_video_samples_remain_in_bounds_for_reanalysis(self):
        from video_optimizer.benefit import _sample_starts

        starts, length = _sample_starts(15, 3, 10, variant=7)
        self.assertEqual(length, 5)
        self.assertEqual(starts, [0.0, 5.0, 10.0])

    def test_progressive_sample_positions_are_stable_as_count_grows(self):
        from video_optimizer.benefit import _progressive_sample_starts

        first, first_length = _progressive_sample_starts(30, 3, 10, 6)
        grown, grown_length = _progressive_sample_starts(30, 4, 10, 6)

        self.assertEqual(first, grown[:3])
        self.assertEqual(first_length, 5)
        self.assertEqual(grown_length, 5)
        self.assertEqual(len(set(grown)), 4)

    def test_progressive_reanalysis_encodes_only_one_added_sample(self):
        source = media()
        source = MediaInfo(source.path, 120.0, source.bit_rate, 1_200_000,
                           source.streams)
        prior = tuple(SampleResult(
            number=index + 1, total=3, start_seconds=float(index * 20),
            output_size=50_000, projected_size=600_000,
            projected_savings_percent=50.0) for index in range(3))

        def fake_run(command, check=False, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"x" * 50_000)
            return type("Result", (), {"returncode": 0})()

        config = Config(sample_count=3, progressive_sample_max=6,
                        sample_duration_seconds=10)
        with patch("video_optimizer.benefit.subprocess.run", side_effect=fake_run) as run:
            result = analyze_benefit(
                source, config, "HandBrakeCLI", prior_samples=prior,
                progressive_target=4)

        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(result.sample_results), 4)
        self.assertEqual([sample.total for sample in result.sample_results], [4] * 4)

    def test_sample_projection_skips_insufficient_savings(self):
        source = media()
        source = MediaInfo(source.path, 100.0, source.bit_rate, 1_000_000,
                           source.streams)
        def fake_run(command, check=False, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"x" * 190_000)
            return type("Result", (), {"returncode": 0})()
        with patch("video_optimizer.benefit.subprocess.run", side_effect=fake_run):
            result = analyze_benefit(source, Config(sample_duration_seconds=20),
                                     "HandBrakeCLI")
        self.assertEqual(result.decision, Decision.SKIP)
        self.assertIn("does not meet", result.reason)

    def test_hdr_uses_automatic_protected_path(self):
        item = media()
        hdr = Stream(0, "video", "h264", width=1920, height=1080,
                     bit_rate=10_000_000, color_transfer="smpte2084")
        item = MediaInfo(item.path, item.duration, item.bit_rate, item.size,
                         (hdr, *item.audios))
        command = build_command(item, Path("temp.mkv"), Config())
        self.assertEqual(command[command.index("--encoder") + 1], "x265_10bit")
        self.assertEqual(command[command.index("--quality") + 1], "22.5")
        self.assertIn("--hdr-dynamic-metadata", command)

    def test_dolby_vision_requests_dynamic_metadata(self):
        item = media()
        dolby = Stream(0, "video", "hevc", width=3840, height=2160,
                       bit_rate=30_000_000,
                       side_data=("DOVI configuration record",),
                       dolby_vision_profile=8)
        item = MediaInfo(item.path, item.duration, item.bit_rate, item.size,
                         (dolby, *item.audios))
        command = build_command(item, Path("temp.mkv"), Config())
        self.assertEqual(command[command.index("--hdr-dynamic-metadata") + 1], "all")

    def test_dolby_vision_profile_5_is_skipped(self):
        dolby = Stream(0, "video", "hevc", width=3840, height=2160,
                       dolby_vision_profile=5)
        item = MediaInfo(Path("p5.mkv"), 100, 20_000_000, 1000, (dolby,))
        self.assertEqual(analyze_benefit(item, Config(), "HandBrakeCLI").decision,
                         Decision.SKIP)

    def test_handbrake_command_uses_auto_crop_and_source_dimension_limits(self):
        source = media()
        subtitle = Stream(2, "subtitle", "subrip")
        source = MediaInfo(source.path, source.duration, source.bit_rate, source.size,
                           (*source.streams, subtitle))
        command = build_command(source, Path("out.mkv"), Config())
        self.assertEqual(command[command.index("--maxWidth") + 1], "1920")
        self.assertEqual(command[command.index("--maxHeight") + 1], "1080")
        self.assertEqual(command[command.index("--crop-mode") + 1], "auto")
        self.assertIn("--auto-anamorphic", command)
        self.assertIn("--vfr", command)
        self.assertIn("--all-subtitles", command)
        self.assertIn("--subtitle-burned=none", command)

    def test_handbrake_sample_uses_point_to_point_range(self):
        command = build_command(media(), Path("sample.mkv"), Config(),
                                start=123.5, duration=20.0,
                                include_subtitles=False)
        self.assertEqual(command[command.index("--start-at") + 1], "seconds:123.500")
        self.assertEqual(command[command.index("--stop-at") + 1], "seconds:20.000")
        self.assertEqual(command[command.index("--subtitle") + 1], "none")

    def test_attachments_are_omitted_with_warning(self):
        source = media()
        attachment = Stream(2, "attachment", "ttf")
        source = MediaInfo(source.path, source.duration, source.bit_rate, source.size,
                           (*source.streams, attachment))
        def fake_run(command, check=False, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"x" * 100_000)
            return type("Result", (), {"returncode": 0})()
        with patch("video_optimizer.benefit.subprocess.run", side_effect=fake_run):
            result = analyze_benefit(source, Config(), "HandBrakeCLI")
        self.assertNotEqual(result.decision, Decision.ERROR)
        self.assertTrue(any("attachment" in warning for warning in result.warnings))

    def test_audio_passthrough_mask_is_configurable(self):
        source = media(audio="flac")
        default_command = build_command(source, Path("out.mkv"), Config())
        mask = default_command[default_command.index("--audio-copy-mask") + 1]
        self.assertNotIn("flac", mask.split(","))
        copy_flac = Config(audio_passthrough_codecs=("aac", "flac"))
        command = build_command(source, Path("out.mkv"), copy_flac)
        self.assertEqual(command[command.index("--audio-copy-mask") + 1],
                         "aac,flac")

    def test_audio_copy_and_conversion_command(self):
        source = media(audio="flac")
        command = build_command(source, Path("temp.mkv"), Config())
        self.assertIn("x265_10bit", command)
        self.assertEqual(command[command.index("--quality") + 1], "22.5")
        self.assertEqual(command[command.index("--aencoder") + 1], "copy")
        self.assertEqual(command[command.index("--audio-fallback") + 1], "av_aac")
        self.assertEqual(command[command.index("--ab") + 1], "384")

    def test_matroska_command_omits_unsupported_data_streams(self):
        source = media()
        data = Stream(2, "data", "bin_data")
        source = MediaInfo(source.path, source.duration, source.bit_rate, source.size,
                           (*source.streams, data))
        command = build_command(source, Path("temp.mkv"), Config())
        self.assertNotIn("--all-attachments", command)

        def fake_run(command, check=False, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(b"x" * 100)
            return type("Result", (), {"returncode": 0})()
        with patch("video_optimizer.benefit.subprocess.run", side_effect=fake_run):
            result = analyze_benefit(source, Config(), "HandBrakeCLI")
        self.assertIn("unsupported data stream", result.warnings[0])

    def test_audio_stream_selection_modes(self):
        video = Stream(0, "video", "h264", width=1920, height=1080)
        first = Stream(1, "audio", "ac3", channels=2, is_default=False)
        default = Stream(2, "audio", "aac", channels=6, is_default=True)
        source = MediaInfo(Path("multi.mkv"), 100, 5_000_000, 1_000_000,
                           (video, first, default))
        default_command = build_command(source, Path("out.mkv"),
                                        Config(audio_stream_mode="default"))
        self.assertEqual(default_command[default_command.index("--audio") + 1], "2")
        first_command = build_command(source, Path("out.mkv"),
                                      Config(audio_stream_mode="first"))
        self.assertEqual(first_command[first_command.index("--audio") + 1], "1")
        all_command = build_command(
            source, Path("out.mkv"), Config(audio_stream_mode="all"))
        self.assertEqual(all_command[all_command.index("--audio") + 1], "1,2")

    def test_first_english_audio_selection_and_fallback(self):
        video = Stream(0, "video", "h264", width=1920, height=1080)
        french = Stream(1, "audio", "ac3", channels=6, language="fra")
        english = Stream(2, "audio", "truehd", channels=8, language="eng")
        source = MediaInfo(Path("languages.mkv"), 100, 5_000_000, 1_000_000,
                           (video, french, english))
        command = build_command(source, Path("out.mkv"),
                                Config(audio_stream_mode="first_english"))
        self.assertEqual(command[command.index("--audio") + 1], "2")

        untagged = MediaInfo(Path("untagged.mkv"), 100, 5_000_000, 1_000_000,
                             (video, french))
        fallback = build_command(untagged, Path("out.mkv"),
                                 Config(audio_stream_mode="first_english"))
        self.assertEqual(fallback[fallback.index("--audio") + 1], "1")

    def test_metadata_is_stripped_by_default_and_can_be_preserved(self):
        stripped = build_command(media(), Path("out.mkv"), Config())
        self.assertIn("--no-metadata", stripped)
        kept = build_command(media(), Path("out.mkv"),
                             Config(preserve_metadata=True))
        self.assertIn("--keep-metadata", kept)

    def test_verify_rejects_resolution_change(self):
        output = media("hevc")
        changed = Stream(0, "video", "hevc", width=1280, height=720)
        output = MediaInfo(output.path, output.duration, output.bit_rate, output.size,
                           (changed, *output.audios))
        with self.assertRaises(RuntimeError):
            verify(media(), output, Config(crop_mode="none"))

    def test_verify_accepts_smaller_auto_cropped_dimensions(self):
        output = media("hevc")
        cropped = Stream(0, "video", "hevc", width=1920, height=802,
                         pixel_format="yuv420p10le")
        output = MediaInfo(output.path, output.duration, output.bit_rate, output.size,
                           (cropped, *output.audios))
        verify(media(), output, Config(crop_mode="auto"))

    def test_handbrake_subtitle_count_collapses_embedded_caption_services(self):
        base = media()
        subtitles = tuple(
            Stream(2 + index, "subtitle", "subrip", language=language)
            for index, language in enumerate(("eng", "fra", "por", "spa")))
        captions = tuple(
            Stream(6 + index, "subtitle", "eia_608") for index in range(4))
        source = MediaInfo(base.path, base.duration, base.bit_rate, base.size,
                           (*base.streams, *subtitles, *captions))

        self.assertEqual(len(source.subtitles), 8)
        self.assertEqual(expected_handbrake_subtitle_count(source), 5)

    def test_verify_accepts_handbrake_caption_consolidation(self):
        base = media()
        source_subtitles = tuple(
            Stream(2 + index, "subtitle", "subrip", language=language)
            for index, language in enumerate(("eng", "fra", "por", "spa")))
        captions = tuple(
            Stream(6 + index, "subtitle", "eia_608") for index in range(4))
        source = MediaInfo(base.path, base.duration, base.bit_rate, base.size,
                           (*base.streams, *source_subtitles, *captions))
        output_video = Stream(0, "video", "hevc", width=1920, height=1080)
        output_subtitles = tuple(
            Stream(2 + index, "subtitle", "ass", language=language)
            for index, language in enumerate(
                ("eng", "fra", "por", "spa", "eng")))
        output = MediaInfo(Path("output.mkv"), base.duration, base.bit_rate,
                           base.size, (output_video, *base.audios,
                                       *output_subtitles))

        verify(source, output, Config(video_bit_depth=8, crop_mode="none"))

        missing = MediaInfo(output.path, output.duration, output.bit_rate,
                            output.size, output.streams[:-1])
        with self.assertRaisesRegex(RuntimeError, "expected 5, found 4"):
            verify(source, missing,
                   Config(video_bit_depth=8, crop_mode="none"))

    def test_verify_allows_extra_handbrake_caption_track(self):
        base = media()
        languages = ("eng", "fra", "por", "spa")
        source_subtitles = tuple(
            Stream(2 + index, "subtitle", "subrip", language=language)
            for index, language in enumerate(languages))
        source = MediaInfo(base.path, base.duration, base.bit_rate, base.size,
                           (*base.streams, *source_subtitles))
        output_video = Stream(0, "video", "hevc", width=1920, height=1080)
        output_subtitles = tuple(
            Stream(2 + index, "subtitle", "ass", language=language)
            for index, language in enumerate((*languages, "eng")))
        output = MediaInfo(Path("output.mkv"), base.duration, base.bit_rate,
                           base.size, (output_video, *base.audios,
                                       *output_subtitles))

        verify(source, output, Config(video_bit_depth=8, crop_mode="none"))

    def test_verify_rejects_missing_ordinary_subtitle_hidden_by_extra_caption(self):
        base = media()
        languages = ("eng", "fra", "por", "spa")
        source_subtitles = tuple(
            Stream(2 + index, "subtitle", "subrip", language=language)
            for index, language in enumerate(languages))
        source = MediaInfo(base.path, base.duration, base.bit_rate, base.size,
                           (*base.streams, *source_subtitles))
        output_video = Stream(0, "video", "hevc", width=1920, height=1080)
        output_subtitles = tuple(
            Stream(2 + index, "subtitle", "ass", language=language)
            for index, language in enumerate(("eng", "por", "spa", "eng")))
        output = MediaInfo(Path("output.mkv"), base.duration, base.bit_rate,
                           base.size, (output_video, *base.audios,
                                       *output_subtitles))

        with self.assertRaisesRegex(RuntimeError, "fra"):
            verify(source, output,
                   Config(video_bit_depth=8, crop_mode="none"))

    def test_unknown_config_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"surprise": True}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_legacy_ffmpeg_settings_are_accepted_during_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"ffmpeg": "ffmpeg.exe",
                                        "dovi_tool": "dovi_tool.exe",
                                        "video_encoder": "libx265"}),
                            encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.encoding_backend, "handbrake")
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["audio_passthrough_codecs"],
                             ["aac", "ac3", "eac3", "truehd", "dts", "dtshd"])
            self.assertEqual(migrated["handbrake_cli"], "HandBrakeCLI")

    def test_first_run_config_and_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            create_default_config(path)
            self.assertTrue(path.exists())
            self.assertTrue((Path(directory) / "input").is_dir())
            self.assertTrue((Path(directory) / "temp").is_dir())
            self.assertFalse((Path(directory) / "output").exists())
            self.assertEqual(load_config(path).output_directory, "")
            self.assertEqual(load_config(path).backup_suffix, ".bak")

    def test_settings_save_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = replace(Config(), crop_mode="none", sample_duration_seconds=12.5,
                             theme="dark")
            save_config(config, path)
            loaded = load_config(path)
            self.assertEqual(loaded.crop_mode, "none")
            self.assertEqual(loaded.sample_duration_seconds, 12.5)
            self.assertEqual(loaded.reanalysis_mode, "progressive")
            self.assertEqual(loaded.progressive_sample_max, 6)
            self.assertEqual(loaded.theme, "dark")

    def test_progressive_max_cannot_be_below_initial_sample_count(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_config(replace(Config(), sample_count=7,
                                    progressive_sample_max=6),
                            Path(directory) / "config.json")

    def test_invalid_theme_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_config(replace(Config(), theme="midnight"),
                            Path(directory) / "config.json")

    def test_progress_percentage_text(self):
        from video_optimizer.gui import _percent_text
        self.assertEqual(_percent_text(42.56), "42.6%")
        self.assertEqual(_percent_text(-2), "0.0%")
        self.assertEqual(_percent_text(102), "100.0%")

    def test_completed_file_forces_progress_to_100_percent(self):
        self.assertEqual(
            _completed_progress("Completed — movie.mkv"),
            (100.0, "Completed — movie.mkv"),
        )

    def test_reverted_queue_item_is_safe_and_can_be_selected_again(self):
        path = Path("movie.mkv")
        live = LiveQueue(Path("."), ready_seconds=0)
        live.items[path] = FileState(path, status="Completed", selected=True)
        live.completed.add(path)
        live.mark_reverted(path)
        self.assertEqual(live.items[path].status, "Reverted")
        self.assertFalse(live.items[path].selected)
        self.assertNotIn(path, live.completed)
        self.assertTrue(live.set_selected(path, True))
        self.assertEqual(live.items[path].status, "Waiting")

    def test_completed_result_leads_with_size_and_flags_larger_output(self):
        from video_optimizer.gui import _completed_result
        text, warning = _completed_result(
            10 * 1024**3, 11 * 1024**3, "0:42:00")
        self.assertTrue(warning)
        self.assertTrue(text.startswith("11.0 GiB output"))
        self.assertIn("1.0 GiB larger than source (10.0%)", text)

    def test_completed_result_ignores_tiny_container_size_difference(self):
        from video_optimizer.gui import _completed_result
        text, warning = _completed_result(1024**3, 1024**3 + 512, "0:10:00")
        self.assertFalse(warning)
        self.assertTrue(text.startswith("1.0 GiB output"))

    def test_rescan_preserves_existing_completed_row_results(self):
        previous = (
            "☰", "☐ Force", "movie.mkv", "Completed", "100.0%", "60.7%",
            "4.4 GiB", "1.7 GiB", "1.6 GiB output • 64.3% saved • 0:16:16",
        )
        self.assertEqual(
            _preserve_completed_refresh(
                previous, "Completed", "—", "—", "1.6 GiB", "—",
                "Completed — 1.6 GiB output • 64.3% saved • 0:16:16"),
            tuple(str(value) for value in previous[4:9]))

    def test_rescan_allows_completed_row_to_change_status(self):
        previous = (
            "☰", "☐ Force", "movie.mkv", "Completed", "100.0%", "60.7%",
            "4.4 GiB", "1.7 GiB", "1.6 GiB output • 64.3% saved • 0:16:16",
        )
        incoming = ("—", "—", "—", "—", "File is no longer present")
        self.assertEqual(
            _preserve_completed_refresh(previous, "Missing", *incoming), incoming)

    def test_program_beside_application_is_preferred(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "ffmpeg"
            helper.touch()
            with patch("video_optimizer.tools.application_directory",
                       return_value=Path(directory)):
                self.assertEqual(resolve_program("ffmpeg"), str(helper))

    def test_4k_rf_and_denoise(self):
        source = media(height=2160)
        video = Stream(0, "video", "h264", width=3840, height=2160,
                       pixel_format="yuv420p")
        source = MediaInfo(source.path, source.duration, source.bit_rate, source.size,
                           (video, *source.audios))
        command = build_command(source, Path("temp.mkv"),
                                Config(denoise_mode="light"))
        self.assertEqual(command[command.index("--quality") + 1], "22.5")
        self.assertIn("--hqdn3d=light", command)

    def test_cropped_4k_uses_2160_rf(self):
        video = Stream(0, "video", "hevc", width=3840, height=1600,
                       pixel_format="yuv420p10le")
        source = MediaInfo(Path("cropped-4k.mkv"), 100, 20_000_000, 1000,
                           (video,))
        command = build_command(source, Path("temp.mkv"), Config())
        self.assertEqual(command[command.index("--quality") + 1], "22.5")

    def test_cropped_1080p_uses_1080_rf(self):
        settings = {"2160": 20.0, "1080": 21.0, "720": 22.0, "480": 23.0}
        video = Stream(0, "video", "h264", width=1920, height=800,
                       pixel_format="yuv420p")
        source = MediaInfo(Path("cropped-1080.mkv"), 100, 5_000_000, 1000,
                           (video,))
        command = build_command(source, Path("temp.mkv"),
                                Config(quality_rf_by_resolution=settings))
        self.assertEqual(command[command.index("--quality") + 1], "21.0")

    def test_progress_bar_keeps_encoding_stats(self):
        line = _format_progress({"out_time_us": "50000000", "frame": "1200",
                                 "fps": "42.5", "total_size": "104857600",
                                 "out_time": "00:00:50.000000",
                                 "bitrate": "16777.2kbits/s", "speed": "1.8x",
                                 "progress": "continue"}, 100.0)
        self.assertIn("50.00%", line)
        self.assertIn("frame=1200", line)
        self.assertIn("fps=42.5", line)
        self.assertIn("size=100.0MiB", line)
        self.assertIn("speed=1.8x", line)

    def test_unattended_is_distinct_from_batch_yes(self):
        unattended = parser().parse_args(["encode", "--unattended"])
        batch = parser().parse_args(["encode", "--yes"])
        self.assertTrue(unattended.unattended)
        self.assertFalse(unattended.yes)
        self.assertTrue(batch.yes)
        self.assertFalse(batch.unattended)

    def test_cli_defaults_to_encode_when_command_is_omitted(self):
        default = parser().parse_args([])
        unattended = parser().parse_args(["--unattended"])
        explicit_scan = parser().parse_args(["scan"])
        self.assertEqual(default.command, "encode")
        self.assertEqual(unattended.command, "encode")
        self.assertTrue(unattended.unattended)
        self.assertEqual(explicit_scan.command, "scan")

    def test_pause_controller_observes_native_handbrake_status(self):
        class FakePopen:
            pid = 123

            def __init__(self):
                self.signals = []
                self.commands = []
                self.stdin = self

            def poll(self):
                return None

            def send_signal(self, value):
                self.signals.append(value)

            def write(self, value):
                self.commands.append(value)

            def flush(self):
                pass

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 0

        class FakeProcess:
            def __init__(self, children=()):
                self._children = list(children)

            def children(self, recursive=True):
                return self._children

        child = FakeProcess()
        target = FakeProcess((child,))
        fake_psutil = type("FakePsutil", (), {
            "Process": staticmethod(lambda pid: target),
            "Error": Exception,
        })
        process = FakePopen()
        controller = PauseController(process, "movie.mkv")
        controller.observe_output("Encoding Paused by user command, 'r' to resume")
        self.assertTrue(controller.pause_confirmed)
        controller.observe_progress()
        self.assertFalse(controller.pause_confirmed)

        cancelling = PauseController(process, "movie.mkv")
        with patch("video_optimizer.pause.psutil", fake_psutil):
            cancelling.cancel(grace_seconds=0)
        self.assertTrue(cancelling.cancelled)
        self.assertEqual(process.commands[-1], "q")


if __name__ == "__main__":
    unittest.main()
