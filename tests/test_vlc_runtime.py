import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_optimizer.vlc_runtime import compatible_windows_vlc, comparison_available, find_windows_vlc


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        (self.folder / "plugins").mkdir()
        (self.folder / "libvlccore.dll").write_bytes(b"fixture")
        data = bytearray(134)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3c, 128)
        data[128:132] = b"PE\0\0"
        struct.pack_into("<H", data, 132, 0x8664)
        (self.folder / "libvlc.dll").write_bytes(data)

    def test_matching_architecture_is_detected_without_loading_dll(self):
        self.assertTrue(compatible_windows_vlc(self.folder, 0x8664))

    def test_wrong_architecture_is_rejected(self):
        self.assertFalse(compatible_windows_vlc(self.folder, 0x14c))

    def test_missing_plugins_disables_detection(self):
        (self.folder / "plugins").rmdir()
        self.assertFalse(compatible_windows_vlc(self.folder, 0x8664))

    def test_missing_binding_disables_comparison(self):
        with patch("video_optimizer.vlc_runtime.importlib.util.find_spec", return_value=None):
            self.assertFalse(comparison_available())

    def test_discovery_skips_incomplete_runtime(self):
        with patch("video_optimizer.vlc_runtime.windows_vlc_candidates", return_value=[self.folder / "missing", self.folder]):
            self.assertEqual(find_windows_vlc(), self.folder)


if __name__ == "__main__":
    unittest.main()
