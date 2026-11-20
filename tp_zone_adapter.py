"""Conservative, read-only TrainingPeaks zone diagnostics and normalization.

No guessed HR bounds. An internal settings endpoint is probed only by an
explicit diagnostic action; it does not return raw athlete settings.
"""
import os
import re
from training_zones import normalize_zones, ZoneValidationError, SPORT_METRICS

_ALLOWED = re.compile(r"^/[a-zA-Z0-9_/{}/.-]+$")
_PLACEHOLDERS = {"athlete_id", "sport", "metric"}
_SETTINGS_PATH = "/fitness/v1/athletes/{athlete_id}/settings"


async def probe_athlete_zone_settings(tp_client, athlete: str):
    """Probe a candidate internal GET endpoint; return metadata, never zone values.

    A 200 response proves endpoint accessibility, NOT that its shape or zones
    are usable. A feature flag prevents accidental probing in production.
    """
    athlete_id = await tp_client._resolve_athlete(athlete)
    if os.getenv("TP_ZONE_SETTINGS_PROBE_ENABLED") != "true":
        return {"available": False, "reason": "probe_disabled", "athlete_id": athlete_id}
    response = await tp_client._request("GET", _SETTINGS_PATH.format(athlete_id=athlete_id))
    result = {"athlete_id": athlete_id, "http_status": response.status_code,
              "available": False, "zones_verified": False}
    if response.status_code != 200:
        result["reason"] = "upstream_error"
        return result
    try:
        payload = response.json()
    except (ValueError, TypeError):
        result["reason"] = "not_json"
        return result
    if not isinstance(payload, dict):
        result["reason"] = "unexpected_payload_type"
        return result
    # Key names are diagnostics, not the user's threshold / private metrics.
    result["top_level_keys"] = sorted(str(k) for k in payload.keys())[:60]
    result["reason"] = "schema_mapping_pending"
    return result


async def get_athlete_training_zones(tp_client, athlete: str, sport: str, metric: str):
    """Read normalized zone records via an operator-verified endpoint only."""
    if sport not in SPORT_METRICS or metric not in SPORT_METRICS[sport]:
        raise ZoneValidationError("Unsupported sport/metric combination")
    athlete_id = await tp_client._resolve_athlete(athlete)
    template = os.getenv("TP_ZONE_ENDPOINT_TEMPLATE", "").strip()
    if not template:
        return {"available": False, "athlete_id": athlete_id, "sport": sport,
                "metric": metric, "reason": "zone_endpoint_not_verified"}
    if (not _ALLOWED.fullmatch(template) or
        set(re.findall(r"{([^{}]+)}", template)) != _PLACEHOLDERS):
        raise ZoneValidationError("Invalid zone endpoint template")
    path = template.format(athlete_id=athlete_id, sport=sport, metric=metric)
    response = await tp_client._request("GET", path)
    if response.status_code != 200:
        return {"available": False, "athlete_id": athlete_id, "sport": sport,
                "metric": metric, "reason": "upstream_error",
                "http_status": response.status_code}
    try:
        zones = normalize_zones(athlete_id, sport, metric, response.json())
    except (ValueError, TypeError) as exc:
        return {"available": False, "athlete_id": athlete_id, "sport": sport,
                "metric": metric, "reason": "invalid_zone_payload",
                "message": str(exc)}
    return {"available": True, **zones}
