import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from video_optimizer.models import MediaInfo, Stream
from video_optimizer.config import Config
from video_optimizer.encoder import selected_audio, build_command, verify
from video_optimizer.gui import OptimizerGUI

class AudioOverrideTests(unittest.TestCase):
    def setUp(self):
        self.audio = (Stream(1, "audio", "aac", channels=2, language="eng"),
                      Stream(2, "audio", "aac", channels=6, language="jpn"),
                      Stream(3, "audio", "aac", channels=2, language="eng", title="Commentary"))
        self.video = Stream(0, "video", "hevc", width=1920, height=1080, pixel_format="yuv420p10le")
        self.media = MediaInfo(Path("movie.mkv"), 100, None, 1000, (self.video, *self.audio))

    def test_custom_keeps_requested_tracks_in_source_order(self):
        self.assertEqual(selected_audio(self.media, "tracks:2,1"), self.audio[:2])

    def test_missing_empty_and_duplicate_tracks_fail(self):
        for mode in ("tracks:", "tracks:4", "tracks:1,1", "tracks:0"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                selected_audio(self.media, mode)

    def test_command_maps_only_selected_audio_and_keeps_subtitle_policy(self):
        config = replace(Config(), audio_stream_mode="tracks:2", crop_mode="none")
        cmd = build_command(self.media, Path("temp.mkv"), config)
        self.assertEqual(cmd[cmd.index("--audio") + 1], "2")
        verify(self.media, replace(self.media, streams=(self.video, self.audio[1])), config)
        with self.assertRaises(RuntimeError):
            verify(self.media, self.media, config)

    def test_settings_reset_removes_override_without_changing_config(self):
        path = self.media.path
        ui = SimpleNamespace(running=False, audio_overrides={}, progressive_samples={path: "old"}, _log=Mock())
        OptimizerGUI._set_audio_override(ui, [path], "all")
        self.assertEqual(ui.audio_overrides[path], "all")
        self.assertNotIn(path, ui.progressive_samples)
        OptimizerGUI._set_audio_override(ui, [path], "settings")
        self.assertNotIn(path, ui.audio_overrides)

    def test_active_queue_cannot_change_policy(self):
        ui = SimpleNamespace(running=True, audio_overrides={})
        OptimizerGUI._set_audio_override(ui, [self.media.path], "all")
        self.assertEqual(ui.audio_overrides, {})
