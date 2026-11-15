import os
import queue
import stat
import tempfile
import threading
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_optimizer.config import Config
from video_optimizer.encoder import (BackupAlreadyExists, EncodeVerificationFailed,
    accept_retained_output, encode, ensure_retained_writable, temporary_output_path)
from video_optimizer.models import MediaInfo, Stream
from video_optimizer.gui import OptimizerGUI
from video_optimizer.retained import record, record_path, verification_record


class RetainedOutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "Junk.Head.2021.mkv"
        self.retained = self.root / ".video-optimizer-Junk.Head.2021-a41c92e7.mkv"
        self.source.write_bytes(b"original movie")
        self.retained.write_bytes(b"encoded movie")
        self.backup = self.source.with_name(self.source.name + ".bak")
        self.config = replace(Config(), crop_mode="none")
        self.video = Stream(0, "video", "h264", width=1920, height=1080)
        self.audio = Stream(1, "audio", "aac", channels=2)
        self.subtitle = Stream(2, "subtitle", "subrip", language="eng")
        self.before = MediaInfo(self.source, 100, None, self.source.stat().st_size,
                                (self.video, self.audio, self.subtitle))
        self.after = replace(self.before, path=self.retained, duration=110,
            streams=(replace(self.video, codec_name="hevc", pixel_format="yuv420p10le"),
                     self.audio, self.subtitle))

    def accept(self, output=None, confirm=lambda _: True):
        record(self.retained, self.source, "retained",
               error="verification failed: duration changed beyond tolerance")
        with patch("video_optimizer.encoder.probe", side_effect=[self.before, self.after]):
            return accept_retained_output(self.source, self.retained,
                                          output or self.source, self.config, confirm=confirm)

    def test_exact_temporary_name_format(self):
        self.retained.unlink()
        with patch("video_optimizer.encoder.uuid.uuid4", return_value=SimpleNamespace(hex="a41c92e7" + "0" * 24)):
            path = temporary_output_path(self.source, self.source)
        self.assertEqual(path.name, ".video-optimizer-Junk.Head.2021-a41c92e7.mkv")
        self.assertEqual(path.parent, self.source.parent)

    def test_long_filename_is_shortened_with_suffix_intact(self):
        path = temporary_output_path(self.source, self.root / ("x" * 250 + ".mp4"))
        self.assertLessEqual(len(str(path).encode("utf-16-le")) // 2, 245)
        self.assertRegex(path.name, r"^\.video-optimizer-x+-[0-9a-f]{8}\.mkv$")

    def test_too_long_directory_is_rejected(self):
        with self.assertRaises(OSError):
            temporary_output_path(Path("/" + "x" * 250) / "movie.mkv", self.source)

    def test_temporary_collision_does_not_overwrite(self):
        with patch("video_optimizer.encoder.uuid.uuid4", return_value=SimpleNamespace(hex="a41c92e7" + "0" * 24)):
            with self.assertRaises(FileExistsError):
                temporary_output_path(self.source, self.source)
        self.assertEqual(self.retained.read_bytes(), b"encoded movie")

    def test_read_only_retained_output_is_corrected(self):
        self.retained.chmod(stat.S_IREAD)
        ensure_retained_writable(self.retained)
        self.assertTrue(self.retained.stat().st_mode & stat.S_IWRITE)

    def test_accept_in_place_preserves_original_backup(self):
        outcome, note = self.accept()
        self.assertEqual(self.backup.read_bytes(), b"original movie")
        self.assertEqual(self.source.read_bytes(), b"encoded movie")
        self.assertFalse(self.retained.exists())
        self.assertEqual(outcome.backup, self.backup)
        self.assertIn("+10.000s", note)

    def test_accept_mp4_to_separate_output(self):
        old = self.source
        self.source = self.root / "movie.mp4"
        old.rename(self.source)
        self.backup = self.source.with_name("movie.mp4.bak")
        self.before = replace(self.before, path=self.source)
        output = self.root / "movie.mkv"
        outcome, _ = self.accept(output)
        self.assertFalse(self.source.exists())
        self.assertEqual(output.read_bytes(), b"encoded movie")
        self.assertEqual(self.backup.read_bytes(), b"original movie")

    def test_existing_backup_blocks_acceptance(self):
        self.backup.write_bytes(b"previous backup")
        with self.assertRaises(BackupAlreadyExists):
            self.accept()
        self.assertEqual(self.backup.read_bytes(), b"previous backup")
        self.assertEqual(self.source.read_bytes(), b"original movie")

    def test_existing_output_blocks_acceptance(self):
        output = self.root / "another.mkv"
        output.write_bytes(b"other movie")
        with self.assertRaises(FileExistsError):
            self.accept(output)
        self.assertEqual(output.read_bytes(), b"other movie")
        self.assertFalse(self.backup.exists())

    def test_commit_failure_preserves_all_video_bytes(self):
        with patch("video_optimizer.encoder.os.replace", side_effect=PermissionError("NAS failure")):
            with self.assertRaises(PermissionError):
                self.accept()
        self.assertEqual(self.source.read_bytes(), b"original movie")
        self.assertEqual(self.backup.read_bytes(), b"original movie")
        self.assertEqual(self.retained.read_bytes(), b"encoded movie")

    def test_backup_copy_failure_preserves_source_and_retained(self):
        with patch("video_optimizer.encoder.shutil.copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.accept()
        self.assertEqual(self.source.read_bytes(), b"original movie")
        self.assertEqual(self.retained.read_bytes(), b"encoded movie")

    def test_review_reports_duration_audio_and_subtitle_differences_before_writing(self):
        self.after = replace(self.after, streams=(self.after.videos[0],))
        confirm = Mock(return_value=False)
        self.assertIsNone(self.accept(confirm=confirm))
        review = confirm.call_args.args[0]
        self.assertIn("audio stream count", review)
        self.assertIn("subtitle stream count", review)
        self.assertIn("eng (1)", review)
        self.assertIn("+10.000s", review)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.source.read_bytes(), b"original movie")
        self.assertTrue(self.retained.is_file())

    def test_completed_output_with_audio_difference_can_be_accepted(self):
        self.after = replace(self.after, streams=(self.after.videos[0], self.subtitle))
        outcome, note = self.accept()
        self.assertEqual(outcome.backup.read_bytes(), b"original movie")
        self.assertIn("audio stream count changed", note)

    def test_partial_output_cannot_be_accepted(self):
        record(self.retained, self.source, "retained",
               error="HandBrakeCLI encoding failed with exit code 4")
        with patch("video_optimizer.encoder.probe") as probe:
            with self.assertRaisesRegex(RuntimeError, "completed encode"):
                accept_retained_output(self.source, self.retained, self.source,
                                       self.config, confirm=lambda _: True)
        probe.assert_not_called()
        self.assertFalse(self.backup.exists())

    def test_output_without_readable_video_cannot_be_accepted(self):
        self.after = replace(self.after, streams=(self.audio,))
        with self.assertRaisesRegex(RuntimeError, "no readable video"):
            self.accept()
        self.assertFalse(self.backup.exists())

    def test_legacy_subtitle_failure_is_eligible_for_its_exact_candidate(self):
        record(self.retained, self.source, "retained",
               error="verification failed: required subtitle language track(s) missing: srp (1)")
        self.assertIsNotNone(verification_record(self.source, self.retained))
        self.assertIsNone(verification_record(self.source, self.root / ".different.mkv"))
        self.assertIsNone(verification_record(self.root / "another.mkv", self.retained))

    def test_verification_records_a_completed_encode_with_all_failures(self):
        def run(command, duration, source, temporary, *args):
            temporary.write_bytes(b"encoded")
        for bad_audio in (False, True):
            after = self.after if not bad_audio else replace(self.after,
                streams=(self.after.videos[0], replace(self.audio, channels=1), self.subtitle))
            with self.subTest(bad_audio=bad_audio), \
                 patch("video_optimizer.encoder._run_handbrake_with_progress", side_effect=run), \
                 patch("video_optimizer.encoder.probe", return_value=after):
                with self.assertRaises(EncodeVerificationFailed) as raised:
                    encode(self.before, self.root, self.root, self.config, False)
                marker = verification_record(self.source, raised.exception.retained_output)
                self.assertTrue(marker["verification_failed"])
                self.assertEqual(marker["final_output"], str(self.source))
                self.assertIn("duration changed", marker["error"])
                self.assertEqual("audio channel count changed" in marker["error"], bad_audio)
                self.assertEqual(self.source.read_bytes(), b"original movie")

    def test_delete_retained_output_never_deletes_source(self):
        gui = SimpleNamespace(retained_failed_outputs={self.source: self.retained},
            running=False, retained_action_busy=False, completed_outputs={}, _log=Mock())
        with patch("video_optimizer.gui.messagebox.askyesno", return_value=True):
            gui.live = None
            gui.retained_action_paths = {}
            gui._reserve_retained_action = lambda *args: OptimizerGUI._reserve_retained_action(gui, *args)
            OptimizerGUI._delete_retained(gui, self.source)
        self.assertFalse(self.retained.exists())
        self.assertEqual(self.source.read_bytes(), b"original movie")
        self.assertFalse(record_path(self.retained).exists())

    def test_delete_refuses_source_alias(self):
        gui = SimpleNamespace(retained_failed_outputs={self.source: self.source},
            running=False, retained_action_busy=False, completed_outputs={})
        with patch("video_optimizer.gui.messagebox.askyesno", return_value=True), \
             patch("video_optimizer.gui.messagebox.showerror") as error:
            gui.live = None
            gui.retained_action_paths = {}
            gui._reserve_retained_action = lambda *args: OptimizerGUI._reserve_retained_action(gui, *args)
            OptimizerGUI._delete_retained(gui, self.source)
        error.assert_called_once()
        self.assertEqual(self.source.read_bytes(), b"original movie")

    def test_changed_video_blocks_acceptance(self):
        def probing(path, _program):
            if path == self.retained:
                self.source.write_bytes(b"changed original")
                return self.after
            return self.before
        record(self.retained, self.source, "retained",
               error="verification failed: duration changed beyond tolerance")
        with patch("video_optimizer.encoder.probe", side_effect=probing):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                accept_retained_output(self.source, self.retained, self.source, self.config,
                                       confirm=lambda _: True)
        self.assertFalse(self.backup.exists())
        self.assertTrue(self.retained.exists())

    def test_source_removal_failure_keeps_output_and_backup(self):
        original_unlink = Path.unlink
        output = self.root / "other.mkv"
        def unlink(path, *args, **kwargs):
            if path == self.source:
                raise PermissionError("NAS removal failed")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", unlink):
            outcome, note = self.accept(output)
        self.assertEqual(output.read_bytes(), b"encoded movie")
        self.assertEqual(self.source.read_bytes(), b"original movie")
        self.assertEqual(self.backup.read_bytes(), b"original movie")
        self.assertIn("removal failed", note)

    def test_filter_rebuild_after_rename_keeps_appended_and_hidden_order(self):
        class Table:
            def __init__(self, rows, hidden):
                self.values = {str(p): ["☰", "☐ Force", p.name, "Failed", "—", "—",
                    "1 KiB", "—", "Failed", "—", "☐ Encode", str(p.parent), "☐ Clean"] for p in rows}
                self.attached = [str(p) for p in rows if p != hidden]
            def get_children(self):
                return tuple(self.attached)
            def exists(self, iid):
                return iid in self.values
            def index(self, iid):
                return self.attached.index(iid) if iid in self.attached else 0
            def selection(self):
                return ()
            def item(self, iid, field=None, **kwargs):
                if "values" in kwargs:
                    self.values[iid] = list(kwargs["values"])
                return self.values[iid] if field == "values" else () if field == "tags" else {}
            def delete(self, iid):
                self.values.pop(iid)
                if iid in self.attached:
                    self.attached.remove(iid)
            def insert(self, parent, index, iid, values, **kwargs):
                self.values[iid] = list(values)
                self.attached.insert(index, iid)
            def move(self, iid, parent, index):
                if iid in self.attached:
                    self.attached.remove(iid)
                self.attached.append(iid)
            def detach(self, iid):
                if iid in self.attached:
                    self.attached.remove(iid)
        first, hidden, appended = [self.root / name for name in ("First.mkv", "Hidden.mkv", "Largest.mkv")]
        renamed = self.root / "Renamed.Largest.mkv"
        table = Table([first, hidden, appended], hidden)
        gui = SimpleNamespace(table=table, live=None, reanalyze_target=None,
            force_encode_paths=set(), clean_paths=set(), file_selection={},
            dropped_entries={}, deleted_backups=set(), completed_outputs={},
            inspection_results={}, inspection_errors={}, inspection_lock=threading.Lock(),
            inspection_queue=queue.Queue(), inspection_enqueued=set(), inspection_cache=Mock(),
            filter_order=[str(first), str(hidden)], filtered_rows={str(hidden)},
            manual_order_active=False, sort_mode_var=SimpleNamespace(get=lambda: "Largest First"),
            _refresh_force_all_heading=Mock(), _refresh_encode_all_heading=Mock(),
            _refresh_clean_all_heading=Mock(), _refresh_missing_button=Mock())
        gui._all_table_rows = lambda: OptimizerGUI._all_table_rows(gui)
        gui.apply_media_view = lambda **kwargs: OptimizerGUI.apply_media_view(gui, **kwargs)
        def visibility(_gui, path, visible, **kwargs):
            if not visible:
                table.detach(str(path))
                gui.filtered_rows.add(str(path))
        with patch.object(OptimizerGUI, "_path_matches_active_filter", side_effect=lambda _g, p: p != hidden), \
             patch.object(OptimizerGUI, "_set_path_filter_visibility", side_effect=visibility), \
             patch("video_optimizer.gui.inspection_sort_key", side_effect=AssertionError("Rename reran sort")):
            OptimizerGUI.paths_renamed(gui, {appended: renamed})
        self.assertEqual(gui.filter_order, [str(first), str(hidden), str(renamed)])
        self.assertEqual(table.attached, [str(first), str(renamed)])

    def test_file_details_show_output_and_preserve_source_and_acceptance(self):
        output = self.root / "output" / "movie.mkv"
        gui = SimpleNamespace(completed_outputs={self.source: output},
            manual_acceptance_notes={self.source: "Manually accepted; difference +10.000s"},
            force_encode_paths=set(), encode_failure_details={}, retained_failed_outputs={},
            inspection_results={}, inspection_errors={})
        values = ["☰", "☐ Force", output.name, "Completed", "100%", "—", "1 KiB",
                  "—", "Completed", "—", "☐ Encode", str(output.parent), "☐ Clean"]
        text = OptimizerGUI._file_details_text(gui, self.source, values)
        self.assertIn(f"Full path: {output}", text)
        self.assertIn(f"Original source: {self.source}", text)
        self.assertIn("difference +10.000s", text)

    def test_manual_acceptance_uses_saved_config_in_background(self):
        record(self.retained, self.source, "retained", verification_failed=True,
               config=asdict(self.config), final_output=str(self.source))
        gui = SimpleNamespace(retained_failed_outputs={self.source: self.retained}, running=False,
            retained_action_busy=False, status_var=Mock(), _emit=Mock())
        def immediate_thread(target, **kwargs):
            return SimpleNamespace(start=target)
        with patch("video_optimizer.gui.messagebox.askyesno", return_value=True), \
             patch("video_optimizer.gui.resolve_program", return_value="ffprobe"), \
             patch("video_optimizer.gui.threading.Thread", side_effect=immediate_thread), \
             patch("video_optimizer.gui.accept_retained_output", return_value=(Mock(), "accepted")) as accept:
            gui.live = None
            gui.retained_action_paths = {}
            gui._log = Mock()
            gui._reserve_retained_action = lambda *args: OptimizerGUI._reserve_retained_action(gui, *args)
            OptimizerGUI._accept_retained(gui, self.source)
        accept.assert_called_once()
        self.assertEqual(accept.call_args.args, (self.source, self.retained, self.source, self.config, "ffprobe"))
        self.assertTrue(callable(accept.call_args.kwargs["confirm"]))
        self.assertEqual(gui._emit.call_args.args[0], "retained_accepted")


if __name__ == "__main__":
    unittest.main()
