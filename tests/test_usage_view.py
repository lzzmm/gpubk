import unittest
from datetime import datetime, timedelta, timezone

from bk.timeparse import to_iso
from bk.usage_view import activity_bar_cells, build_activity_trends


class UsageViewTests(unittest.TestCase):
    def test_trends_count_reserved_time_once_and_keep_four_weeks(self):
        end = datetime(2030, 1, 28, 12, tzinfo=timezone.utc)
        record = {
            "window_start": to_iso(end - timedelta(days=1)),
            "status": "ok",
            "active_observed_seconds": 900,
            "observed_seconds": 1800,
        }

        daily, weekly = build_activity_trends([record], end)

        self.assertEqual(len(daily), 7)
        self.assertEqual(len(weekly), 4)
        self.assertEqual(sum(values[0] for _, values in daily), 900)
        self.assertEqual(sum(values[1] for _, values in daily), 1800)
        self.assertEqual(sum(values[0] for _, values in weekly), 900)
        self.assertEqual(sum(values[1] for _, values in weekly), 1800)

    def test_activity_bar_never_exceeds_reserved_width(self):
        active, idle = activity_bar_cells(9, 4, 4, 16)

        self.assertEqual(active, 16)
        self.assertEqual(idle, 0)


if __name__ == "__main__":
    unittest.main()
