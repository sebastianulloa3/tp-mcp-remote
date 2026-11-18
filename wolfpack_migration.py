"""WOLFPACK migration tools. Read-only until explicitly reviewed.
Backups are returned as JSON; caller must persist them in private storage.
"""
import os
import json
import hashlib
from datetime import datetime, timezone
import httpx

def register_migration_tools(mcp, tp_get):
    async def fetch(url):
        return await tp_get(url)

    @mcp.tool()
    async def wp_export_library_json(library_id: int, include_structure: bool = True) -> dict:
        """Export complete TrainingPeaks library to a portable JSON payload (read only)."""
        root = "https://tpapi.trainingpeaks.com"
        items = await fetch(f"{root}/exerciselibrary/v2/libraries/{library_id}/items") or []
        workouts = []
        for item in items:
            record = {
                "source": "trainingpeaks", "source_id": item.get("exerciseLibraryItemId"),
                "library_id": library_id, "title": item.get("itemName"),
                "sport_id": item.get("workoutTypeId"),
                "duration_hours": item.get("totalTimePlanned"),
                "distance_m": item.get("distancePlanned"),
                "tss": item.get("tssPlanned"), "intensity_factor": item.get("ifPlanned"),
                "description": item.get("description"), "coach_comments": item.get("coachComments"),
            }
            if include_structure:
                record["structure"] = item.get("structure")
            workouts.append(record)
        payload = {"schema_version": 1, "source": "trainingpeaks",
                   "kind": "library", "library_id": library_id,
                   "exported_at": datetime.now(timezone.utc).isoformat(),
                   "count": len(workouts), "workouts": workouts}
        stable = json.dumps(workouts, sort_keys=True, ensure_ascii=False, default=str)
        payload["sha256"] = hashlib.sha256(stable.encode()).hexdigest()
        return payload

    @mcp.tool()
    async def wp_export_plan_json(plan_id: int) -> dict:
        """Export original plan metadata and sessions to JSON (read only)."""
        root = "https://tpapi.trainingpeaks.com"
        plan = await fetch(f"{root}/plans/v1/plans/{plan_id}")
        start = (plan.get("startDate") or "")[:10]
        end = (plan.get("endDate") or "")[:10]
        if not start or not end:
            return {"error": "Plan has no explicit start/end date; review manually", "plan_id": plan_id}
        workouts = await fetch(f"{root}/plans/v1/plans/{plan_id}/workouts/{start}/{end}") or []
        return {"schema_version": 1, "source": "trainingpeaks", "kind": "plan",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "plan": plan, "workouts": workouts, "count": len(workouts)}

    @mcp.tool()
    async def wp_intervals_connection_test() -> dict:
        """Test Intervals.icu API key without changing any data."""
        key = os.environ.get("INTERVALS_API_KEY")
        if not key:
            return {"connected": False, "reason": "INTERVALS_API_KEY not configured"}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get("https://intervals.icu/api/v1/athlete/0",
                                 auth=httpx.BasicAuth("API_KEY", key))
        return {"connected": r.status_code == 200, "http_status": r.status_code,
                "message": "Read-only connection test; no data changed"}

    @mcp.tool()
    async def wp_migration_manifest(source_records: list[dict]) -> dict:
        """Build a no-write migration manifest with stable IDs and duplicates flagged."""
        seen = set()
        rows = []
        for rec in source_records:
            ident = f"{rec.get('source', 'trainingpeaks')}:{rec.get('source_id', rec.get('workout_id'))}"
            duplicate = ident in seen
            seen.add(ident)
            rows.append({"external_id": ident, "title": rec.get("title"),
                         "sport": rec.get("sport_id", rec.get("sport")),
                         "duplicate": duplicate, "status": "requires_mapping_review",
                         "write_performed": False})
        return {"dry_run": True, "count": len(rows), "items": rows}
