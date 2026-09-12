"""Tests for the shared exponential-backoff helper."""
import unittest

from utils import backoff


class NextDelayTests(unittest.TestCase):
    def test_no_failures_uses_the_base_interval(self):
        self.assertEqual(backoff.next_delay(30, 0, cap=900), 30)

    def test_each_consecutive_failure_doubles_the_delay(self):
        self.assertEqual(backoff.next_delay(30, 1, cap=900), 60)
        self.assertEqual(backoff.next_delay(30, 2, cap=900), 120)
        self.assertEqual(backoff.next_delay(30, 3, cap=900), 240)

    def test_delay_is_clamped_to_the_cap(self):
        self.assertEqual(backoff.next_delay(30, 50, cap=900), 900)

    def test_absurd_failure_counts_return_the_cap_without_hanging(self):
        # A naive ``base * 2 ** failures`` would build a multi-megabyte integer here.
        self.assertEqual(backoff.next_delay(30, 10_000_000, cap=900), 900)

    def test_negative_failure_counts_are_treated_as_none(self):
        self.assertEqual(backoff.next_delay(30, -5, cap=900), 30)

    def test_a_cap_below_the_base_wins(self):
        self.assertEqual(backoff.next_delay(30, 0, cap=10), 10)

    def test_non_positive_base_falls_back_to_a_sane_interval(self):
        self.assertGreater(backoff.next_delay(0, 0, cap=900), 0)
        self.assertGreater(backoff.next_delay(-1, 3, cap=900), 0)


if __name__ == "__main__":
    unittest.main()
