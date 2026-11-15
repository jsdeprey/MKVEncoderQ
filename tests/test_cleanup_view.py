import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_optimizer.config import Config
from video_optimizer.gui import OptimizerGUI
from video_optimizer.retained import record, remove_record


class CleanupViewTests(unittest.TestCase):
    def test_cleanup_overrides_rating_and_tracks_files_after_a_fresh_load(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            converted = root / "Converted.mkv"
            failed = root / "Failed.mkv"
            ordinary = root / "Ordinary.mkv"
            active = root / "Active.mkv"
            backup = root / "Converted.mp4.bak"
            retained = root / ".video-optimizer-Failed-abcd1234.mkv"
            active_output = root / ".video-optimizer-Active-abcd1234.mkv"
            for path in (converted, failed, ordinary, active, backup, retained, active_output):
                path.touch()
            record(retained, failed, "retained", verification_failed=True)
            record(active_output, active, "active")

            rows = {str(path): {"status": "Waiting", "backup": "—"}
                    for path in (converted, failed, ordinary, active)}

            def table_set(iid, column, value=None):
                if value is not None:
                    rows[iid][column] = value
                return rows[iid].get(column)

            cleanup = True
            gui = SimpleNamespace(
                cleanup_var=SimpleNamespace(get=lambda: cleanup),
                _cleanup_visible_paths=set(), run_config=Config(),
                table=SimpleNamespace(set=table_set),
                completed_outputs={}, retained_failed_outputs={},
                _marked_filename=lambda path: path.name,
                codec_filter_var=SimpleNamespace(get=lambda: "All"),
                resolution_filter_var=SimpleNamespace(get=lambda: "All"),
                rating_filter_var=SimpleNamespace(get=lambda: "Wasteful"),
                inspection_results={ordinary: object()})
            gui._existing_backup = lambda *args: OptimizerGUI._existing_backup(gui, *args)
            gui._retained_candidates = lambda *args: OptimizerGUI._retained_candidates(gui, *args)

            def visible():
                return {Path(iid) for iid in rows
                        if OptimizerGUI._path_matches_active_filter(gui, Path(iid))}

            with patch("psutil.pid_exists", return_value=True):
                self.assertEqual(visible(), {converted, failed})
                self.assertEqual(rows[str(converted)]["backup"], "[ Present ]")
                self.assertIn("Failed encode retained", rows[str(failed)]["file"])

                backup.unlink()
                retained.unlink()
                remove_record(retained)
                self.assertEqual(visible(), set())
                self.assertEqual(rows[str(converted)]["backup"], "—")

            cleanup = False
            with patch("video_optimizer.gui.inspection_matches", return_value=True):
                self.assertEqual(visible(), {ordinary})

    def test_converted_backup_does_not_attach_if_source_exists_or_match_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            encoded = root / "Movie.mkv"
            source = root / "Movie.avi"
            backup = root / "Movie.avi.bak"
            encoded.touch()
            source.touch()
            backup.touch()
            self.assertIsNone(OptimizerGUI._existing_backup(None, encoded, Config(), {}))
            source.unlink()
            self.assertEqual(OptimizerGUI._existing_backup(None, encoded, Config(), {}), backup)
            (root / "Movie.mp4.bak").touch()
            self.assertIsNone(OptimizerGUI._existing_backup(None, encoded, Config(), {}))

    def test_cleanup_cannot_start_an_encode(self):
        gui = SimpleNamespace(running=False, cleanup_var=SimpleNamespace(get=lambda: True))
        with patch("video_optimizer.gui.messagebox.showinfo") as notice:
            OptimizerGUI.start(gui)
        notice.assert_called_once()
