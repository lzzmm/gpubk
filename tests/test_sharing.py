import unittest

from bk.sharing import (
    inferred_share_memory_mb,
    parse_share_units,
    reservation_share_units,
    share_units_for_peer_limit,
)


class SharingTests(unittest.TestCase):
    def test_fraction_percentage_and_unit_inputs_use_one_capacity_model(self):
        self.assertEqual(parse_share_units("3/4", 4), 3)
        self.assertEqual(parse_share_units("75%", 4), 3)
        self.assertEqual(parse_share_units("3", 4), 3)

    def test_unrepresentable_fraction_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "whole capacity units"):
            parse_share_units("3/4", 2)

    def test_share_with_leaves_one_minimum_unit_per_peer(self):
        self.assertEqual(share_units_for_peer_limit(1, 4), 3)
        self.assertEqual(share_units_for_peer_limit(2, 4), 2)
        self.assertEqual(share_units_for_peer_limit(3, 4), 1)

    def test_legacy_reservation_defaults_to_one_unit_and_invalid_data_fails_closed(self):
        self.assertEqual(reservation_share_units({}, 4), 1)
        self.assertEqual(reservation_share_units({"share_units": "bad"}, 4), 4)
        self.assertEqual(reservation_share_units({"share_units": 1.5}, 4), 4)

    def test_inferred_memory_scales_with_reserved_share(self):
        self.assertEqual(inferred_share_memory_mb(24000, 4, 1), 6000)
        self.assertEqual(inferred_share_memory_mb(24000, 4, 3), 18000)


if __name__ == "__main__":
    unittest.main()
