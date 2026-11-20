import unittest
from training_zones import normalize_zones, zone_target, ZoneValidationError

BASE = {"athlete_id": 6664356, "sport": "running", "metric": "hr",
        "unit": "bpm", "source": "trainingpeaks", "updated_at": "2026-10-24",
        "zones": [{"name": "Z2", "lower": 130, "upper": 145}]}


class ZoneTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(zone_target(normalize_zones(6664356, "running", "hr", BASE), "Z2")["upper"], 145)

    def test_wrong_athlete(self):
        with self.assertRaises(ZoneValidationError):
            normalize_zones(999, "running", "hr", BASE)

    def test_wrong_sport(self):
        with self.assertRaises(ZoneValidationError):
            normalize_zones(6664356, "cycling", "hr", BASE)

    def test_missing_zones(self):
        with self.assertRaises(ZoneValidationError):
            normalize_zones(6664356, "running", "hr", {**BASE, "zones": []})

    def test_wrong_units(self):
        with self.assertRaises(ZoneValidationError):
            normalize_zones(6664356, "running", "hr", {**BASE, "unit": "sec/km"})

    def test_invalid_bounds(self):
        with self.assertRaises(ZoneValidationError):
            normalize_zones(6664356, "running", "hr", {**BASE, "zones": [{"name":"Z2","lower":150,"upper":130}]})


if __name__ == "__main__":
    unittest.main()
