import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from video_optimizer.combined_gui import CombinedApp
from video_optimizer.gui import OptimizerGUI, _logical_rename_mapping
from video_optimizer.rename_gui import RenameGUI, execute_rename_plan

class ReleaseTests(unittest.TestCase):
    def test_backup_extensions_and_exact_undo(self):
        for extension in (".mp4", ".avi", ".mpg", ".m4v", ".mkv"):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, output, new = root/('Old'+extension), root/'Old.mkv', root/'New.mkv'
                backup = root/('Old'+extension+'.bak')
                output.write_bytes(b'encoded'); backup.write_bytes(b'original')
                app = SimpleNamespace(encoder=SimpleNamespace(completed_outputs={source: output}))
                with patch('video_optimizer.combined_gui.load_config', return_value=SimpleNamespace(backup_suffix='.bak')):
                    companions = CombinedApp.rename_companion_plan(app, {output:new})
                renamed_backup = root/('New'+extension+'.bak')
                self.assertEqual(companions, {backup:renamed_backup})
                logical = _logical_rename_mapping({output:new}, {source:output}, {source})
                self.assertEqual(logical, {source:new.with_suffix(extension)})
                transaction = execute_rename_plan({output:new, **companions})
                ui = SimpleNamespace(last_rename={output:new}, last_rename_all=transaction,
                    companion_plan_callback=Mock(side_effect=AssertionError('must use recorded undo')),
                    busy_callback=lambda path:(False,''), renamed_callback=Mock(), _replace_paths=Mock(),
                    undo_button=Mock(), status_var=Mock(), activity_log=None)
                with patch.object(RenameGUI, '_append_activity_log'):
                    RenameGUI.undo(ui)
                self.assertEqual(backup.read_bytes(), b'original')
                self.assertEqual(output.read_bytes(), b'encoded')
                self.assertFalse(renamed_backup.exists())

    def retry_ui(self):
        path=Path('copying.mp4')
        ui=SimpleNamespace(inspection_generation=2, inspection_errors={path:'Permission denied'},
            inspection_cancel=threading.Event(), inspect_new_files=True, table=Mock(),
            inspection_enqueued=set(), _log=Mock(), queue_inspection=Mock(), after=Mock())
        return path,ui

    def test_exhausted_temporary_failure_is_reinspected(self):
        path,ui=self.retry_ui()
        OptimizerGUI._retry_deferred_inspection(ui,path,2)
        ui.queue_inspection.assert_called_once_with([path],force=True)

    def test_success_cancel_and_old_directory_stop_deferred_retry(self):
        for kind in ('success','cancel','generation'):
            path,ui=self.retry_ui()
            if kind=='success': ui.inspection_errors.clear()
            if kind=='cancel': ui.inspection_cancel.set()
            OptimizerGUI._retry_deferred_inspection(ui,path,1 if kind=='generation' else 2)
            ui.queue_inspection.assert_not_called()

    def test_inspection_off_defers_instead_of_discarding_retry(self):
        path,ui=self.retry_ui(); ui.inspect_new_files=False
        OptimizerGUI._retry_deferred_inspection(ui,path,2)
        ui.after.assert_called_once()
        ui.queue_inspection.assert_not_called()
