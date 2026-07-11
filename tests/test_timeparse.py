import unittest
from datetime import datetime, timedelta, timezone

from bk.timeparse import normalize_queue_start, parse_duration_seconds, parse_friendly_start, parse_memory_mb


class TimeParsingTests(unittest.TestCase):
    def test_compound_duration(self):
        self.assertEqual(parse_duration_seconds("1h30m"), 90 * 60)
        self.assertEqual(parse_duration_seconds("1d2h5m"), (24 * 60 + 2 * 60 + 5) * 60)

    def test_invalid_compound_duration(self):
        for value in ("1h30h", "1hour", "30", "", "0m"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_duration_seconds(value)

    def test_memory_units(self):
        self.assertEqual(parse_memory_mb("12g"), 12 * 1024)
        self.assertEqual(parse_memory_mb("1.5GiB"), 1536)
        self.assertEqual(parse_memory_mb("4096m"), 4096)

    def test_friendly_now_uses_the_active_five_minute_interval(self):
        now = datetime(2030, 1, 1, 12, 41, 23, tzinfo=timezone.utc)

        self.assertEqual(parse_friendly_start("now", now), datetime(2030, 1, 1, 12, 40, tzinfo=timezone.utc))

    def test_friendly_relative_time_rounds_forward(self):
        now = datetime(2030, 1, 1, 12, 41, 23, tzinfo=timezone.utc)

        self.assertEqual(parse_friendly_start("+30m", now), datetime(2030, 1, 1, 13, 15, tzinfo=timezone.utc))

    def test_queue_start_floors_now_but_ceils_a_future_value(self):
        now = datetime(2030, 1, 1, 12, 41, 23, tzinfo=timezone.utc)

        self.assertEqual(normalize_queue_start(now, now), datetime(2030, 1, 1, 12, 40, tzinfo=timezone.utc))
        self.assertEqual(
            normalize_queue_start(now + timedelta(minutes=1), now),
            datetime(2030, 1, 1, 12, 45, tzinfo=timezone.utc),
        )

    def test_friendly_clock_and_calendar_forms_are_aligned(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)

        for value in ("20:00", "tomorrow 09:00", "07-13 20:00", "+1h"):
            with self.subTest(value=value):
                parsed = parse_friendly_start(value, now)
                self.assertEqual(int(parsed.timestamp()) % 300, 0)
                self.assertGreaterEqual(parsed, now - timedelta(minutes=5))

    def test_friendly_time_error_lists_supported_examples(self):
        with self.assertRaisesRegex(ValueError, "tomorrow 09:00"):
            parse_friendly_start("hwo", datetime(2030, 1, 1, tzinfo=timezone.utc))

        with self.assertRaisesRegex(ValueError, "00, 05"):
            parse_friendly_start("2030-01-01T12:41:00Z", datetime(2030, 1, 1, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
