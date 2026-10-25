from __future__ import annotations

import tempfile
import threading
import unittest
import subprocess
import json
import stat
from pathlib import Path
from unittest.mock import patch

from mkvcleaner_core.cleaner import build_clean_command, build_mkvpropedit_command
from mkvcleaner_core.config import CleanerConfig, load_config
from mkvcleaner_core.metadata import _run_json
from mkvcleaner_core.models import MediaFile, TrackInfo
from mkvcleaner_core.process import run_tool
from mkvcleaner_core.scanner import scan_iter
from mkvcleaner_core.workers import ensure_cleaner_writable, process_files


class ScannerTests(unittest.TestCase):
    def test_incremental_recursive_discovery_and_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.mkv").touch()
            (root / "ignore.txt").touch()
            nested = root / "nested"
            nested.mkdir()
            (nested / "b.MKV").touch()

            stop_event = threading.Event()
            iterator = scan_iter(root, True, stop_event=stop_event)
            first = next(iterator)
            stop_event.set()

            self.assertEqual(first.path.name, "a.mkv")
            self.assertEqual(list(iterator), [])


class CleanerProcessTests(unittest.TestCase):
    @staticmethod
    def _subtitle_media(audio_language="eng", *, subtitle_language="eng",
                        forced=False, default=True):
        return MediaFile(Path("movie.mkv"), tracks=[
            TrackInfo(0, "video"),
            TrackInfo(1, "audio", language=audio_language, default_track=True),
            TrackInfo(2, "subtitles", language=subtitle_language, default_track=default,
                      forced_track=forced),
        ])

    def test_english_audio_disables_ordinary_default_subtitle(self) -> None:
        media = self._subtitle_media()
        media.add_reason("Default subtitles (1)")
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), CleanerConfig())
        self.assertIn("flag-default=0", command)

    def test_foreign_audio_preserves_default_subtitle(self) -> None:
        media = self._subtitle_media("jpn")
        media.add_reason("metadata")
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), CleanerConfig())
        self.assertNotIn("flag-default=0", command)

    def test_unknown_audio_language_disables_default_subtitle(self) -> None:
        media = self._subtitle_media("")
        media.add_reason("metadata")
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), CleanerConfig())
        self.assertIn("flag-default=0", command)

    def test_english_forced_subtitle_flags_are_preserved(self) -> None:
        media = self._subtitle_media(forced=True)
        media.add_reason("metadata")
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), CleanerConfig())
        self.assertNotIn("flag-default=0", command)
        self.assertNotIn("flag-forced=0", command)

    def test_foreign_forced_subtitle_flags_are_cleared_for_english_audio(self) -> None:
        media = self._subtitle_media(
            subtitle_language="nld", forced=True)
        media.add_reason("metadata")
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), CleanerConfig())
        self.assertIn("flag-default=0", command)
        self.assertIn("flag-forced=0", command)

    def test_foreign_audio_preserves_foreign_forced_subtitle_flags(self) -> None:
        media = self._subtitle_media(
            "jpn", subtitle_language="eng", forced=True)
        media.add_reason("metadata")
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), CleanerConfig())
        self.assertNotIn("flag-default=0", command)
        self.assertNotIn("flag-forced=0", command)

    def test_explicit_option_clears_default_flag_for_foreign_audio(self) -> None:
        media = self._subtitle_media("jpn")
        media.add_reason("Subtitle Default flags (1)")
        config = CleanerConfig(clear_all_subtitle_default_flags=True)
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), config)
        self.assertIn("flag-default=0", command)

    def test_explicit_option_clears_english_forced_flag(self) -> None:
        media = self._subtitle_media(forced=True)
        media.add_reason("Subtitle Forced flags (1)")
        config = CleanerConfig(clear_all_subtitle_forced_flags=True)
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), config)
        self.assertIn("flag-forced=0", command)

    def test_explicit_options_do_nothing_when_flags_are_already_clear(self) -> None:
        media = self._subtitle_media(forced=False, default=False)
        config = CleanerConfig(clear_all_subtitle_default_flags=True,
                               clear_all_subtitle_forced_flags=True)
        with patch("mkvcleaner_core.cleaner.locate_mkvpropedit",
                   return_value=Path("mkvpropedit.exe")):
            command = build_mkvpropedit_command(
                media, Path("mkvmerge.exe"), config)
        self.assertNotIn("flag-default=0", command)
        self.assertNotIn("flag-forced=0", command)

    def test_track_name_cleanup_can_target_audio_only(self) -> None:
        media = MediaFile(Path("movie.mkv"), tracks=[
            TrackInfo(0, "video", name="Main Video"),
            TrackInfo(1, "audio", name="Dumb Release Name"),
            TrackInfo(2, "subtitles", name="English Signs"),
        ])
        media.add_reason("Track names (1)")
        config = CleanerConfig(remove_audio_track_names=True)
        command = build_clean_command(media, Path("mkvmerge.exe"), config)
        self.assertIn("1:", command)
        self.assertNotIn("0:", command)
        self.assertNotIn("2:", command)

    def test_old_combined_track_name_setting_enables_all_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps({"remove_track_names": True}), encoding="utf-8")
            config = load_config(path)
        self.assertTrue(config.remove_video_track_names)
        self.assertTrue(config.remove_audio_track_names)
        self.assertTrue(config.remove_subtitle_track_names)

    def test_tool_launch_does_not_inherit_console_input(self) -> None:
        completed = subprocess.CompletedProcess(["mkvmerge"], 0, "{}", "")
        with patch("mkvcleaner_core.process.subprocess.run",
                   return_value=completed) as launched:
            run_tool(["mkvmerge", "--version"])
        self.assertIs(launched.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_inspection_launch_error_identifies_mkvmerge_stage(self) -> None:
        error = OSError(50, "The request is not supported")
        with patch("mkvcleaner_core.metadata.run_tool", side_effect=error):
            with self.assertRaisesRegex(
                    RuntimeError, "Unable to start mkvmerge inspection"):
                _run_json(Path("mkvmerge.exe"), Path("movie.mkv"))

    def test_cleaner_preflight_clears_read_only_before_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "movie.mkv"
            path.write_bytes(b"mkv")
            path.chmod(path.stat().st_mode & ~0o222)
            media = MediaFile(path)

            self.assertTrue(ensure_cleaner_writable(media))

            self.assertTrue(path.stat().st_mode & stat.S_IWRITE)
            self.assertEqual(
                media.preflight_changes, ["Cleared read-only attribute"])

    def test_cleaner_preflight_leaves_writable_file_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "movie.mkv"
            path.write_bytes(b"mkv")
            media = MediaFile(path)

            self.assertFalse(ensure_cleaner_writable(media))
            self.assertEqual(media.preflight_changes, [])


class PipelineControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = [MediaFile(Path(f"file-{index}.mkv")) for index in range(3)]
        self.config = CleanerConfig(threads=1, dry_run=True)

    def test_cancel_stops_new_inspections(self) -> None:
        stop_event = threading.Event()
        inspected: list[MediaFile] = []
        statuses_at_callback: list[str] = []

        def fake_inspect(media, _mkvmerge, _config):
            media.clean_reasons.append("metadata")

        def inspected_callback(media):
            inspected.append(media)
            statuses_at_callback.append(media.status)
            if len(inspected) == 1:
                stop_event.set()

        with patch("mkvcleaner_core.workers.inspect_one", side_effect=fake_inspect):
            results = process_files(
                self.files,
                Path("mkvmerge.exe"),
                self.config,
                inspect_callback=inspected_callback,
                stop_event=stop_event,
            )

        self.assertEqual(len(inspected), 3)
        self.assertEqual(statuses_at_callback.count("Cancelled"), 2)
        self.assertEqual(len(results), 3)

    def test_pause_blocks_before_inspection(self) -> None:
        run_event = threading.Event()
        waiting = threading.Event()
        finished = threading.Event()

        def fake_inspect(media, _mkvmerge, _config):
            media.clean_reasons.clear()

        def run():
            with patch("mkvcleaner_core.workers.inspect_one", side_effect=fake_inspect):
                process_files(
                    self.files[:1],
                    Path("mkvmerge.exe"),
                    self.config,
                    inspect_wait_callback=lambda _media: waiting.set(),
                    run_event=run_event,
                )
            finished.set()

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(waiting.wait(1))
        self.assertFalse(finished.is_set())
        run_event.set()
        thread.join(1)
        self.assertTrue(finished.is_set())


if __name__ == "__main__":
    unittest.main()
