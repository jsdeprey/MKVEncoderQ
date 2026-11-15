import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from video_optimizer.rename_gui import RenameGUI

class CombinedRenameTests(unittest.TestCase):
    def test_success_orders_rename_reset_clear(self):
        events = []
        ui = SimpleNamespace(checked_paths=lambda: [Path("old.mkv")],
            _apply_paths=lambda paths: events.append("rename") or {paths[0]: Path("new.mkv")},
            reset_controls=lambda: events.append("reset"), remove_all=lambda: events.append("clear"))
        RenameGUI.rename_reset_remove_all(ui)
        self.assertEqual(events, ["rename", "reset", "clear"])

    def test_cancel_failure_or_excluded_busy_keeps_list_and_controls(self):
        ui = SimpleNamespace(checked_paths=lambda: [Path("old.mkv")],
            _apply_paths=Mock(return_value=None), reset_controls=Mock(), remove_all=Mock())
        RenameGUI.rename_reset_remove_all(ui)
        ui.reset_controls.assert_not_called()
        ui.remove_all.assert_not_called()

    def test_no_checked_files_does_not_clear(self):
        ui = SimpleNamespace(checked_paths=lambda: [], _apply_paths=Mock(), reset_controls=Mock(), remove_all=Mock())
        with patch("video_optimizer.rename_gui.messagebox.showinfo"):
            RenameGUI.rename_reset_remove_all(ui)
        ui._apply_paths.assert_not_called()
        ui.remove_all.assert_not_called()
