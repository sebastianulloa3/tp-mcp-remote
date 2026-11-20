import os
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from tp_zone_adapter import get_athlete_training_zones, probe_athlete_zone_settings
from training_zones import ZoneValidationError

class ZoneAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(_resolve_athlete=AsyncMock(return_value=123),
                                      _request=AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {
                                          "athlete_id": 123, "sport": "running", "metric": "hr",
                                          "unit": "bpm", "source": "trainingpeaks", "updated_at": "2026-11-20",
                                          "zones": [{"name": "Z2", "lower": 130, "upper": 145}]
                                      })))
    async def test_athlete_only_endpoint(self):
        with patch.dict(os.environ, {"TP_ZONE_ENDPOINT_TEMPLATE": "/fitness/v1/athletes/{athlete_id}/settings"}):
            result = await get_athlete_training_zones(self.client, "Nicolas", "running", "hr")
        self.assertTrue(result["available"])
        self.client._request.assert_awaited_once_with("GET", "/fitness/v1/athletes/123/settings")

    async def test_missing_endpoint_fails_closed(self):
        with patch.dict(os.environ, {"TP_ZONE_ENDPOINT_TEMPLATE": ""}):
            result = await get_athlete_training_zones(self.client, "Nicolas", "running", "hr")
        self.assertFalse(result["available"])
        self.client._request.assert_not_awaited()

    async def test_unknown_placeholder_rejected(self):
        with patch.dict(os.environ, {"TP_ZONE_ENDPOINT_TEMPLATE": "/athletes/{athlete_id}/{secret}"}):
            with self.assertRaises(ZoneValidationError):
                await get_athlete_training_zones(self.client, "Nicolas", "running", "hr")

    async def test_wrong_athlete_payload_rejected(self):
        self.client._request.return_value = SimpleNamespace(status_code=200, json=lambda: {
            "athlete_id": 999, "sport": "running", "metric": "hr",
            "unit": "bpm", "source": "trainingpeaks", "updated_at": "2026-11-20",
            "zones": [{"name": "Z2", "lower": 130, "upper": 145}]
        })
        with patch.dict(os.environ, {"TP_ZONE_ENDPOINT_TEMPLATE": "/athletes/{athlete_id}/settings"}):
            result = await get_athlete_training_zones(self.client, "Nicolas", "running", "hr")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "invalid_zone_payload")

if __name__ == "__main__":
    unittest.main()

class ZoneSettingsProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(
            _resolve_athlete=AsyncMock(return_value=6664356),
            _request=AsyncMock(return_value=SimpleNamespace(
                status_code=200,
                json=lambda: {"heartRateZones": [{"lower": 123}], "email": "private@example.com"})))

    async def test_probe_off_no_request(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await probe_athlete_zone_settings(self.client, "Nicolas Gil")
        self.assertEqual(result["reason"], "probe_disabled")
        self.client._request.assert_not_awaited()

    async def test_metadata_only(self):
        with patch.dict(os.environ, {"TP_ZONE_SETTINGS_PROBE_ENABLED": "true"}):
            result = await probe_athlete_zone_settings(self.client, "Nicolas Gil")
        self.assertEqual(result["http_status"], 200)
        self.assertFalse(result["zones_verified"])
        self.assertNotIn("private@example.com", str(result))
        self.assertNotIn("123", str(result))
        self.client._request.assert_awaited_once_with(
            "GET", "/fitness/v1/athletes/6664356/settings")

    async def test_upstream_403(self):
        self.client._request.return_value = SimpleNamespace(status_code=403)
        with patch.dict(os.environ, {"TP_ZONE_SETTINGS_PROBE_ENABLED": "true"}):
            result = await probe_athlete_zone_settings(self.client, "Nicolas Gil")
        self.assertEqual(result["http_status"], 403)
        self.assertFalse(result["available"])
