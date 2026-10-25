import tempfile
import time
import unittest
from pathlib import Path

from video_optimizer.drop import expand_dropped_paths
from video_optimizer.queueing import LiveQueue


class DragDropTests(unittest.TestCase):
    def test_folder_drop_is_recursive_filtered_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            first = root / "first.mkv"
            second = nested / "second.MP4"
            ignored = nested / "notes.txt"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            ignored.write_text("no", encoding="utf-8")

            result = expand_dropped_paths(
                [root, first], {".mkv", ".mp4"})

            self.assertEqual(
                {path for path, _source_root in result},
                {first.resolve(), second.resolve()},
            )
            self.assertTrue(all(source_root == root.resolve()
                                for _path, source_root in result))

    def test_single_file_drop_uses_parent_as_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            movie = Path(directory) / "movie.mkv"
            movie.write_bytes(b"movie")
            self.assertEqual(
                expand_dropped_paths([movie], {".mkv"}),
                [(movie.resolve(), movie.parent.resolve())],
            )

    def test_manual_queue_preserves_output_root_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            scan_root = base / "scan"
            drop_root = base / "drop"
            scan_root.mkdir()
            drop_root.mkdir()
            movie = drop_root / "movie.mkv"
            movie.write_bytes(b"movie")
            queue = LiveQueue(scan_root, 0)

            added = queue.add_manual([(movie, drop_root), (movie, drop_root)])

            self.assertEqual(len(added), 1)
            self.assertEqual(queue.source_root(movie), drop_root.resolve())
            queue.refresh(now=time.monotonic() + 1)
            self.assertEqual(queue.claim_waiting().path, movie.resolve())


if __name__ == "__main__":
    unittest.main()
