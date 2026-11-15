import unittest
from pathlib import Path
from video_optimizer.queueing import LiveQueue, FileState

class QueueRestartTests(unittest.TestCase):
    def test_reanalysis_stop_restart_preserves_nineteen_rows(self):
        queue = LiveQueue(Path('/movies'), 0)
        paths = [Path(f'/movies/episode-{i}.mkv') for i in range(19)]
        for path in paths:
            queue.items[path] = FileState(path, status='Waiting', selected=True)
        first = queue.claim_waiting()
        queue.mark_done(first.path, 'Cancelled', 'Cancelled by user')
        self.assertTrue(queue.queue_reanalysis(first.path))
        queue.set_allowed_paths({first.path})
        self.assertEqual(queue.claim_waiting().path, first.path)
        queue.mark_done(first.path, 'Cancelled', 'Stopped')
        self.assertIsNone(queue.claim_waiting())
        queue.set_allowed_paths(None)
        self.assertEqual(len(queue.snapshot()), 19)
        self.assertTrue(all(queue.items[path].selected for path in paths[1:]))
        self.assertEqual(queue.claim_waiting().path, paths[1])
        self.assertFalse(any(path in queue.completed for path in paths[1:]))

    def test_restriction_never_claims_other_waiting_or_reanalysis_files(self):
        queue = LiveQueue(Path('/movies'), 0)
        a,b=Path('/movies/a.mkv'),Path('/movies/b.mkv')
        queue.items[a]=FileState(a,status='Failed')
        queue.items[b]=FileState(b,status='Waiting')
        queue.queue_reanalysis(a)
        queue.set_allowed_paths({b})
        self.assertEqual(queue.claim_waiting().path,b)
        self.assertIsNone(queue.claim_waiting())
        queue.set_allowed_paths(None)
        self.assertEqual(queue.claim_waiting().path,a)
