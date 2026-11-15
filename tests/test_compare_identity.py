import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from video_optimizer.compare_player import CompareWindow


class IdentityTests(unittest.TestCase):
    def test_toggle_updates_headings_filenames_and_window_titles(self):
        hidden = Mock()
        labels = [(Mock(), Mock()), (Mock(), Mock())]
        windows = [Mock(), Mock()]
        ui = SimpleNamespace(hide_identity=hidden, identity_labels=labels,
                             detached=windows, paths=(Path('source.mkv.bak'), Path('encoded.mkv')))
        hidden.get.return_value = True
        CompareWindow.update_identities(ui)
        labels[0][0].configure.assert_called_with(text='Video 1')
        labels[1][0].configure.assert_called_with(text='Video 2')
        for heading, filename in labels:
            filename.configure.assert_called_with(text='')
        windows[0].title.assert_called_with('Video 1 — use the shared controls')
        hidden.get.return_value = False
        CompareWindow.update_identities(ui)
        labels[0][0].configure.assert_called_with(text='ORIGINAL BACKUP')
        labels[1][1].configure.assert_called_with(text='encoded.mkv')
        windows[0].title.assert_called_with('Original — use the shared controls')
