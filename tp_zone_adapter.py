"""Read-only TrainingPeaks zone adapter.

Requires a verified TP_ZONE_ENDPOINT_TEMPLATE configured by the operator.
No endpoint is guessed and no athlete data is modified.
"""
import os
import re
from training_zones import normalize_zones, ZoneValidationError

_ALLOWED = re.compile(r"^/[a-zA-Z0-9_/{}/.-]+$")
_PLACEHOLDERS = {"athlete_id", "sport", "metric"}


async def get_athlete_training_zones(tp_client, athlete: str, sport: str, metric: str):
    athlete_id = await tp_client._resolve_athlete(athlete)
    template = os.getenv("TP_ZONE_ENDPOINT_TEMPLATE", "").strip()
    if not template:
        return {"available": False, "athlete_id": athlete_id, "sport": sport,
                "metric": metric, "reason": "zone_endpoint_not_verified",
                "message": "Configurar TP_ZONE_ENDPOINT_TEMPLATE solo después de verificar un endpoint real de TrainingPeaks."}
    if not _ALLOWED.fullmatch(template):
        raise ZoneValidationError("Invalid zone endpoint template")
    placeholders = set(re.findall(r"{([^{}]+)}", template))
    if "athlete_id" not in placeholders or not placeholders.issubset(_PLACEHOLDERS):
        raise ZoneValidationError("Invalid zone endpoint placeholders")
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
