"""Validation of athlete-specific TrainingPeaks zones (no API endpoint assumptions).

This module is intentionally independent of the transport. An adapter must
fetch actual zone records and pass them through normalize_zones.
"""
from __future__ import annotations

from typing import Any

SPORT_METRICS = {
    "cycling": {"hr": "bpm", "power": "W"},
    "running": {"hr": "bpm", "pace": "sec/km", "power": "W"},
    "swimming": {"pace": "sec/100m", "hr": "bpm"},
}


class ZoneValidationError(ValueError):
    pass


def normalize_zones(
    athlete_id: int, sport: str, metric: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Fail closed: never synthesize zones, thresholds, or source dates."""
    if sport not in SPORT_METRICS or metric not in SPORT_METRICS[sport]:
        raise ZoneValidationError("Unsupported sport/metric combination")
    if not isinstance(raw, dict):
        raise ZoneValidationError("Missing athlete zone record")
    if str(raw.get("athlete_id")) != str(athlete_id):
        raise ZoneValidationError("Zone record belongs to another athlete")
    if raw.get("sport") != sport or raw.get("metric") != metric:
        raise ZoneValidationError("Zone record has mismatched sport or metric")
    expected_unit = SPORT_METRICS[sport][metric]
    if raw.get("unit") != expected_unit:
        raise ZoneValidationError(f"Expected zone unit {expected_unit}")
    if not raw.get("source") or not raw.get("updated_at"):
        raise ZoneValidationError("Missing source or zone update date")
    zones = raw.get("zones")
    if not isinstance(zones, list) or not zones:
        raise ZoneValidationError("No zones available")
    result = []
    seen = set()
    for zone in zones:
        if not isinstance(zone, dict):
            raise ZoneValidationError("Malformed zone")
        name = zone.get("name")
        low, high = zone.get("lower"), zone.get("upper")
        if not isinstance(name, str) or not name.strip() or name in seen:
            raise ZoneValidationError("Duplicate or unnamed zone")
        if (isinstance(low, bool) or isinstance(high, bool)
            or not isinstance(low, (int, float))
            or not isinstance(high, (int, float))
            or low <= 0 or high <= low):
            raise ZoneValidationError("Invalid zone boundaries")
        seen.add(name)
        result.append({"name": name, "lower": low, "upper": high})
    return {
        "athlete_id": athlete_id,
        "sport": sport,
        "metric": metric,
        "unit": expected_unit,
        "source": raw["source"],
        "updated_at": raw["updated_at"],
        "threshold": raw.get("threshold"),
        "zones": result,
    }


def zone_target(record: dict[str, Any], name: str) -> dict[str, Any]:
    """Return actual target boundaries; error if the requested zone is absent."""
    for zone in record["zones"]:
        if zone["name"].casefold() == name.casefold():
            return {"metric": record["metric"], "unit": record["unit"],
                    "lower": zone["lower"], "upper": zone["upper"]}
    raise ZoneValidationError(f"Zone {name} not configured")
