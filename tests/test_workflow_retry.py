import queue
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_optimizer.gui import OptimizerGUI
from video_optimizer.inspection import inspect_with_retry, transient_inspection_error, inspect_media
from video_optimizer.models import MediaInfo
from video_optimizer.queueing import LiveQueue, FileState
from video_optimizer.config import Config
from video_optimizer.retained import record


class WorkflowRetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.failed = self.root / "failed.mkv"
        self.other = self.root / "other.mkv"
        self.live = LiveQueue(self.root, 0)
        self.live.items[self.failed] = FileState(self.failed, status="Failed")
        self.live.items[self.other] = FileState(self.other, status="Encoding")

    def gui(self):
        gui = SimpleNamespace(live=self.live, running=True, retained_action_paths={},
            retained_failed_outputs={self.failed: self.root / ".failed.mkv"},
            completed_outputs={}, _log=Mock(), _emit=Mock(), worker=object(),
            encode_active=threading.Event(), current_cancel_event=object())
        gui._reserve_retained_action = lambda *args: OptimizerGUI._reserve_retained_action(gui, *args)
        return gui

    def test_accept_runs_while_another_file_encodes_without_replacing_worker(self):
        gui = self.gui()
        worker, cancel = gui.worker, gui.current_cancel_event
        record(gui.retained_failed_outputs[self.failed], self.failed, "retained",
               verification_failed=True, config=asdict(Config()), final_output=str(self.failed))
        with patch("video_optimizer.gui.messagebox.askyesno", return_value=True), \
             patch("video_optimizer.gui.resolve_program", return_value="ffprobe"), \
             patch("video_optimizer.gui.accept_retained_output", return_value=(Mock(), "accepted")), \
             patch("video_optimizer.gui.threading.Thread", side_effect=lambda target, **kw: SimpleNamespace(start=target)):
            OptimizerGUI._accept_retained(gui, self.failed)
        self.assertEqual(gui._emit.call_args.args[0], "retained_accepted")
        self.assertIs(gui.worker, worker)
        self.assertIs(gui.current_cancel_event, cancel)
        self.assertTrue(gui.running)
        self.assertEqual(self.live.items[self.other].status, "Encoding")

    def test_delete_runs_while_another_file_encodes(self):
        gui = self.gui()
        self.failed.write_bytes(b"original")
        retained = gui.retained_failed_outputs[self.failed]
        retained.write_bytes(b"encode")
        with patch("video_optimizer.gui.messagebox.askyesno", return_value=True):
            OptimizerGUI._delete_retained(gui, self.failed)
        self.assertFalse(retained.exists())
        self.assertEqual(self.failed.read_bytes(), b"original")
        self.assertTrue(gui.running)
        self.assertFalse(self.live.retained_reserved)

    def test_same_file_active_blocks_actions(self):
        gui = self.gui()
        self.live.items[self.failed].status = "Encoding"
        with patch("video_optimizer.gui.messagebox.askyesno") as ask:
            OptimizerGUI._accept_retained(gui, self.failed)
            OptimizerGUI._delete_retained(gui, self.failed)
        ask.assert_not_called()

    def test_reserved_file_cannot_be_claimed_but_unrelated_queue_continues(self):
        self.live.items[self.failed].status = "Waiting"
        self.live.items[self.other].status = "Waiting"
        self.assertTrue(self.live.reserve_retained({self.failed}))
        self.assertEqual(self.live.claim_waiting().path, self.other)
        self.assertIsNone(self.live.claim_waiting())
        self.live.release_retained({self.failed})
        self.assertEqual(self.live.claim_waiting().path, self.failed)

    def test_reservation_and_encoder_claim_are_mutually_exclusive(self):
        self.live.items[self.failed].status = "Waiting"
        self.assertEqual(self.live.claim_waiting().path, self.failed)
        self.assertFalse(self.live.reserve_retained({self.failed}))

    def test_cleaner_cannot_reserve_acceptance_files(self):
        self.assertTrue(self.live.reserve_retained({self.failed}))
        self.assertFalse(self.live.reserve_for_cleaning([self.failed])[0])

    def test_double_action_on_same_file_is_blocked(self):
        gui = self.gui()
        retained = gui.retained_failed_outputs[self.failed]
        self.assertIsNotNone(gui._reserve_retained_action(self.failed, retained))
        self.assertIsNone(gui._reserve_retained_action(self.failed, retained))

    def test_last_locked_file_recovers_without_further_directory_events(self):
        cancel = Mock()
        cancel.is_set.return_value = False
        cancel.wait.return_value = False
        inspection = Mock(side_effect=[PermissionError("Permission denied"), "success"])
        self.assertEqual(inspect_with_retry(self.failed, "ffprobe", cancel, inspection), "success")
        self.assertEqual(inspection.call_count, 2)
        cancel.wait.assert_called_once_with(2)

    def test_persistent_permission_failure_is_bounded(self):
        cancel = Mock()
        cancel.is_set.return_value = False
        cancel.wait.return_value = False
        inspection = Mock(side_effect=PermissionError("denied"))
        with self.assertRaises(PermissionError):
            inspect_with_retry(self.failed, "ffprobe", cancel, inspection)
        self.assertEqual(inspection.call_count, 6)
        self.assertEqual(cancel.wait.call_count, 5)

    def test_cancel_interrupts_retry_wait(self):
        cancel = Mock()
        cancel.is_set.return_value = False
        cancel.wait.return_value = True
        inspection = Mock(side_effect=PermissionError("denied"))
        self.assertIsNone(inspect_with_retry(self.failed, "ffprobe", cancel, inspection))
        self.assertEqual(inspection.call_count, 1)

    def test_permanent_error_does_not_retry(self):
        cancel = Mock()
        cancel.is_set.return_value = False
        inspection = Mock(side_effect=ValueError("ffprobe was not found"))
        with self.assertRaises(ValueError):
            inspect_with_retry(self.failed, "ffprobe", cancel, inspection)
        cancel.wait.assert_not_called()

    def test_explicit_inspect_clears_stale_error_and_cache(self):
        gui = SimpleNamespace(live=self.live, inspection_results={},
            inspection_errors={self.failed: "Permission denied"},
            inspection_lock=threading.Lock(), inspection_enqueued=set(),
            inspection_total=0, inspection_queue=queue.Queue(),
            inspection_cache=Mock(), inspection_cycle_active=True,
            cancel_inspection_button=Mock())
        self.assertEqual(OptimizerGUI.queue_inspection(gui, [self.failed]), 0)
        self.assertEqual(OptimizerGUI.queue_inspection(gui, [self.failed], force=True), 1)
        self.assertNotIn(self.failed, gui.inspection_errors)
        gui.inspection_cache.discard.assert_called_once_with(self.failed)
        self.assertEqual(gui.inspection_queue.get_nowait(), self.failed)
        self.assertEqual(OptimizerGUI.queue_inspection(gui, [self.failed], force=True), 0)

    def test_copy_change_during_probe_is_not_cached_as_a_success(self):
        self.failed.write_bytes(b"partial")
        def probing(path, program, **kwargs):
            path.write_bytes(b"finished full file")
            return MediaInfo(path, 10, None, 7, ())
        with patch("video_optimizer.inspection.probe", side_effect=probing):
            with self.assertRaisesRegex(RuntimeError, "still be copying"):
                inspect_media(self.failed, "ffprobe")

    def test_ffprobe_permission_message_is_transient(self):
        self.assertTrue(transient_inspection_error(RuntimeError("movie.mkv: Permission denied")))

    def test_explicit_inspect_bypasses_success_cache(self):
        gui = SimpleNamespace(live=self.live, inspection_results={self.failed: Mock()},
            inspection_errors={}, inspection_lock=threading.Lock(), inspection_enqueued=set(),
            inspection_total=0, inspection_queue=queue.Queue(), inspection_cache=Mock(),
            inspection_cycle_active=True, cancel_inspection_button=Mock())
        self.assertEqual(OptimizerGUI.queue_inspection(gui, [self.failed], force=True), 1)
        self.assertNotIn(self.failed, gui.inspection_results)


if __name__ == "__main__":
    unittest.main()
