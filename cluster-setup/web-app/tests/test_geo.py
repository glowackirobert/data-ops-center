"""Unit tests for app.geo.truncate_path_at (pure geometry, no I/O)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.geo import truncate_path_at


class TruncatePathAtTests(unittest.TestCase):

    def test_truncates_at_matching_point_on_a_segment(self):
        # A straight line running east along the equator; target sits exactly
        # halfway along the middle segment.
        path = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]
        result = truncate_path_at(path, (0.0, 1.5))  # (lat, lon)
        self.assertEqual(result[0], [0.0, 0.0])
        self.assertEqual(result[1], [1.0, 0.0])
        self.assertAlmostEqual(result[2][0], 1.5, places=6)
        self.assertAlmostEqual(result[2][1], 0.0, places=6)

    def test_target_at_the_very_start_collapses_to_one_point(self):
        path = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]
        result = truncate_path_at(path, (0.0, 0.0))
        self.assertAlmostEqual(result[-1][0], 0.0, places=6)

    def test_target_beyond_the_end_keeps_the_whole_path(self):
        path = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]
        result = truncate_path_at(path, (0.0, 5.0))
        self.assertEqual(len(result), len(path))
        self.assertAlmostEqual(result[-1][0], 2.0, places=6)

    def test_out_and_back_path_prefers_the_furthest_along_tie(self):
        # The path goes out to (1,0) and back to (0,0): a target sitting near
        # the shared segment is equally close to both directions' segments.
        # truncate_path_at should keep the LATER (further-along) match, since
        # this is used to trim a shape to its scheduled *last* stop.
        path = [[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]]
        result = truncate_path_at(path, (0.0, 0.5))
        # furthest-along candidate is on segment index 1 (the return leg)
        self.assertGreaterEqual(len(result), 3)

    def test_never_returns_fewer_points_than_a_valid_path_needs(self):
        path = [[18.60, 54.35], [18.61, 54.36], [18.62, 54.37], [18.63, 54.38]]
        result = truncate_path_at(path, (54.365, 18.615))
        self.assertGreaterEqual(len(result), 2)


if __name__ == '__main__':
    unittest.main()
