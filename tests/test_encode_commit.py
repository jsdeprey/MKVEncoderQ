"""Exercise encode's filesystem commit with real files and mocked media tools."""
import os
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from video_optimizer.config import Config
from video_optimizer.encoder import EncodeCancelled, EncodeVerificationFailed, encode
from video_optimizer.models import MediaInfo, Stream


class EncodeCommitTests(unittest.TestCase):
    def exercise(self, suffix, separate, failure=None, backup=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory) / "input"
            root.mkdir()
            source = root / ("movie" + suffix)
            source.write_bytes(b"original")
            output_root = Path(directory) / "output" if separate else root
            output = output_root / "movie.mkv"
            original = MediaInfo(source, 100.0, 1000, 8, (
                Stream(0, "video", "h264", width=1920, height=1080),))
            config = Config()
            encoded = replace(original, path=output, streams=(
                Stream(0, "video", "hevc", width=1920, height=1080,
                       pixel_format="yuv420p10le"),))
            events = []
            temporary_paths = []
            real_replace, real_unlink = os.replace, Path.unlink

            def run(command, duration, source_name, temporary, *args):
                temporary_paths.append(temporary)
                temporary.write_bytes(b"verified output")
                if failure == "encode":
                    raise RuntimeError("injected encoding failure")
                if failure == "cancel":
                    raise EncodeCancelled("injected cancellation")

            def probe(*args):
                if failure == "probe":
                    raise RuntimeError("injected probe failure")
                events.append("probe")
                if failure == "verify":
                    return original  # H.264 must fail real HEVC verification.
                return encoded

            def commit(temporary, destination):
                self.assertEqual(source.read_bytes() if not backup else
                                 source.with_name(source.name + config.backup_suffix).read_bytes(),
                                 b"original")
                self.assertEqual(events, ["probe"])
                events.append("commit")
                if failure == "commit":
                    raise OSError("injected commit failure")
                return real_replace(temporary, destination)

            def unlink(path, *args, **kwargs):
                if path == source:
                    self.assertNotEqual(source, output,
                                        "same-path replacement must not unlink source")
                    self.assertEqual(output.read_bytes(), b"verified output")
                    self.assertEqual(events, ["probe", "commit"])
                    events.append("delete")
                    if failure == "delete":
                        raise OSError("injected deletion failure")
                return real_unlink(path, *args, **kwargs)

            stack.enter_context(patch("video_optimizer.encoder._run_handbrake_with_progress", side_effect=run))
            stack.enter_context(patch("video_optimizer.encoder.probe", side_effect=probe))
            stack.enter_context(patch("video_optimizer.encoder.os.replace", side_effect=commit))
            stack.enter_context(patch.object(Path, "unlink", unlink))
            if failure:
                error = (EncodeVerificationFailed if failure == "verify" else
                         EncodeCancelled if failure == "cancel" else
                         OSError if failure in {"commit", "delete"} else RuntimeError)
                with self.assertRaises(error):
                    encode(original, root, output_root, config, backup)
                self.assertEqual(source.read_bytes(), b"original")
                if failure == "delete":
                    self.assertEqual(output.read_bytes(), b"verified output")
                    self.assertFalse(temporary_paths[0].exists())
                else:
                    if output != source:
                        self.assertFalse(output.exists())
                    self.assertEqual(temporary_paths[0].exists(), failure != "cancel")
                if failure in {"encode", "probe", "verify", "cancel"}:
                    self.assertNotIn("commit", events)
                if failure != "delete":
                    self.assertNotIn("delete", events)
            else:
                outcome = encode(original, root, output_root, config, backup)
                self.assertEqual(outcome.output, output)
                self.assertEqual(outcome.bytes_after, len(b"verified output"))
                self.assertEqual(output.read_bytes(), b"verified output")
                self.assertFalse(temporary_paths[0].exists())
                if source != output:
                    self.assertFalse(source.exists())
                if backup:
                    self.assertEqual(outcome.backup.read_bytes(), b"original")
                else:
                    self.assertIsNone(outcome.backup)
                    self.assertFalse(source.with_name(source.name + config.backup_suffix).exists())

    def test_no_backup_success(self):
        for suffix in (".mkv", ".mp4"):
            for separate in (False, True):
                with self.subTest(suffix=suffix, separate=separate):
                    self.exercise(suffix, separate)

    def test_failures_preserve_original(self):
        for suffix in (".mkv", ".mp4"):
            for separate in (False, True):
                for failure in ("encode", "probe", "verify", "commit", "cancel"):
                    with self.subTest(suffix=suffix, separate=separate, failure=failure):
                        self.exercise(suffix, separate, failure)

    def test_deletion_failure_preserves_committed_output_and_original(self):
        for suffix, separate in ((".mp4", False), (".mp4", True), (".mkv", True)):
            with self.subTest(suffix=suffix, separate=separate):
                self.exercise(suffix, separate, "delete")

    def test_backup_behavior_unchanged(self):
        for suffix in (".mkv", ".mp4"):
            for separate in (False, True):
                with self.subTest(suffix=suffix, separate=separate):
                    self.exercise(suffix, separate, backup=True)


if __name__ == "__main__":
    unittest.main()
