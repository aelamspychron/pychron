import unittest
from math import inf

from pychron.experiment.automated_run.plot_limits import calculate_xmax, extend_xmax


class CalculateXmaxTestCase(unittest.TestCase):
    def test_peak_center_delay_included(self):
        # 100 counts * 1s, but a 120 s peak center ran after time zero
        xmax = calculate_xmax(
            current_max=0, total_seconds=100, elapsed=120, remaining_seconds=100
        )
        self.assertGreaterEqual(xmax, 220)

    def test_no_delay_matches_counts_estimate(self):
        xmax = calculate_xmax(
            current_max=0, total_seconds=100, elapsed=0, remaining_seconds=100
        )
        self.assertEqual(xmax, 125)

    def test_never_shrinks(self):
        xmax = calculate_xmax(
            current_max=500, total_seconds=100, elapsed=0, remaining_seconds=100
        )
        self.assertEqual(xmax, 500)

    def test_does_not_compound(self):
        # calling twice for the same collection (sniff + isotope graph) is stable
        a = calculate_xmax(0, 100, 10, 100)
        b = calculate_xmax(a, 100, 10, 100)
        self.assertEqual(a, b)

    def test_infinite_current_max(self):
        self.assertEqual(calculate_xmax(inf, 100, 0, 100), 125)


class ExtendXmaxTestCase(unittest.TestCase):
    def test_point_past_xmax_extends(self):
        self.assertEqual(extend_xmax(100, 120), 150)

    def test_point_inside_xmax_no_change(self):
        self.assertIsNone(extend_xmax(100, 80))

    def test_infinite_xmax_left_alone(self):
        self.assertIsNone(extend_xmax(inf, 80))


if __name__ == "__main__":
    unittest.main()
