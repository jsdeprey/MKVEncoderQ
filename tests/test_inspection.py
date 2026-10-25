import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_optimizer.inspection import (
    InspectionCache, MediaInspection, h264_waste_rating,
    h265_size_review_rating,
    inspection_matches, inspection_sort_key,
)
from video_optimizer.probe import probe


def result(path: Path, *, codec="h264", width=1920, height=1080,
           bit_rate=500_000, rating="WASTEFUL", size=1000, duration=7200):
    return MediaInspection(path.resolve(), size, 1, codec, width, height,
                           bit_rate, duration, rating)


class InspectionTests(unittest.TestCase):
    def test_h264_waste_thresholds_match_nas_workflow(self):
        self.assertEqual(h264_waste_rating("h264", 3840, 2160, 2_100_000), "WASTEFUL")
        self.assertEqual(h264_waste_rating("h264", 1920, 1080, 500_000), "WASTEFUL")
        self.assertEqual(h264_waste_rating("h264", 1280, 720, 150_000), "WASTEFUL")
        self.assertEqual(h264_waste_rating("h264", 1920, 1080, 300_000), "OK")
        self.assertEqual(h264_waste_rating("h264", 1920, 1080, None), "UNKNOWN")
        self.assertEqual(h264_waste_rating("hevc", 1920, 1080, 9_000_000), "—")

    def test_codec_resolution_and_rating_filters_combine(self):
        item = result(Path("movie.mkv"))
        self.assertTrue(inspection_matches(item, "H.264", "1080p", "Wasteful"))
        self.assertTrue(inspection_matches(item, "All", "1080p or Lower", "All"))
        self.assertFalse(inspection_matches(item, "H.265 / HEVC", "All", "All"))
        four_k = result(Path("4k.mkv"), width=3840, height=2160)
        self.assertFalse(inspection_matches(four_k, "All", "1080p or Lower", "All"))

    def test_h265_review_size_scales_with_duration_and_resolution(self):
        gib = 1024 ** 3
        self.assertEqual(
            h265_size_review_rating("hevc", 1920, 1080, 6 * gib, 7200),
            "REVIEW SIZE")
        self.assertEqual(
            h265_size_review_rating("hevc", 1920, 1080, 4 * gib, 7200),
            "OK")
        self.assertEqual(
            h265_size_review_rating("hevc", 1920, 1080, 3 * gib, 3600),
            "REVIEW SIZE")
        self.assertEqual(
            h265_size_review_rating("hevc", 3840, 2160, 14 * gib, 7200),
            "OK")
        self.assertEqual(
            h265_size_review_rating("hevc", 1920, 1080, 6 * gib, None),
            "UNKNOWN")
        self.assertEqual(
            h265_size_review_rating("h264", 1920, 1080, 6 * gib, 7200),
            "—")

    def test_review_size_rating_is_filterable(self):
        item = result(Path("large-hevc.mkv"), codec="hevc",
                      rating="REVIEW SIZE")
        self.assertTrue(inspection_matches(item, "H.265 / HEVC", "All",
                                           "Review Size"))
        self.assertFalse(inspection_matches(item, "H.265 / HEVC", "All", "OK"))

    def test_sort_modes_are_deterministic(self):
        small = result(Path("small.mkv"), size=100, rating="OK")
        large = result(Path("large.mkv"), size=1000, rating="WASTEFUL")
        self.assertLess(inspection_sort_key(large, "Largest First", "large.mkv"),
                        inspection_sort_key(small, "Largest First", "small.mkv"))
        self.assertLess(inspection_sort_key(small, "Smallest First", "small.mkv"),
                        inspection_sort_key(large, "Smallest First", "large.mkv"))
        self.assertLess(inspection_sort_key(large, "Most Wasteful", "large.mkv"),
                        inspection_sort_key(small, "Most Wasteful", "small.mkv"))

    def test_cache_requires_matching_size_and_modified_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            movie = root / "movie.mkv"
            movie.write_bytes(b"video")
            stat = movie.stat()
            cached = MediaInspection(movie.resolve(), stat.st_size, stat.st_mtime_ns,
                                     "h264", 1920, 1080, 500_000, 7200,
                                     "WASTEFUL")
            cache = InspectionCache(root / "cache.json")
            cache.put(cached)
            self.assertEqual(cache.get(movie), cached)
            movie.write_bytes(b"changed video")
            self.assertIsNone(cache.get(movie))

    def test_malformed_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache.json"
            cache_path.write_text("not json", encoding="utf-8")
            cache = InspectionCache(cache_path)
            self.assertIsNone(cache.get(Path(directory) / "missing.mkv"))

    @patch("video_optimizer.probe.subprocess.run")
    def test_probe_timeout_has_clear_error(self, run):
        run.side_effect = subprocess.TimeoutExpired(["ffprobe"], 45)
        with self.assertRaisesRegex(RuntimeError, "timed out after 45 seconds"):
            probe(Path("movie.mkv"), "ffprobe", timeout=45)

    def test_encoder_inventory_ui_contains_batch_controls(self):
        source = (Path(__file__).parents[1] / "video_optimizer" / "gui.py").read_text(
            encoding="utf-8")
        self.assertIn("Inspect files when added", source)
        self.assertIn("Send All Eligible Files to Cleaner...", source)
        self.assertIn("Send All Files in This Directory to Cleaner...", source)
        self.assertIn('self.table.bind("<Control-a>"', source)
        self.assertIn('("Sort", self.sort_mode_var', source)


if __name__ == "__main__":
    unittest.main()
