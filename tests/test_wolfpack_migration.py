import asyncio
import hashlib
import json
import os
from unittest.mock import patch
from wolfpack_migration import register_migration_tools

class FakeMCP:
    def __init__(self): self.tools = {}
    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator

def run(coro): return asyncio.run(coro)

def test_library_export_is_read_only_and_preserves_structure():
    calls = []
    async def tp_get(url):
        calls.append(url)
        return [{"exerciseLibraryItemId": 17, "itemName": "Tempo",
                 "workoutTypeId": 2, "totalTimePlanned": 1.0,
                 "structure": {"steps": [{"duration": 600, "power": 85}]}}]
    m = FakeMCP()
    register_migration_tools(m, tp_get)
    data = run(m.tools["wp_export_library_json"](123))
    assert data["count"] == 1
    assert data["workouts"][0]["structure"]["steps"][0]["power"] == 85
    assert len(data["sha256"]) == 64
    assert len(calls) == 1 and "/libraries/123/items" in calls[0]

def test_manifest_flags_duplicate_and_never_writes():
    async def tp_get(url): raise AssertionError("unexpected network")
    m = FakeMCP()
    register_migration_tools(m, tp_get)
    result = run(m.tools["wp_migration_manifest"]([
        {"source_id": 1, "title": "A"},
        {"source_id": 1, "title": "A"}]))
    assert result["dry_run"] is True
    assert [x["duplicate"] for x in result["items"]] == [False, True]
    assert all(not x["write_performed"] for x in result["items"])

def test_connection_without_key_does_not_call_api():
    async def tp_get(url): raise AssertionError("unexpected network")
    m = FakeMCP()
    register_migration_tools(m, tp_get)
    with patch.dict(os.environ, {"INTERVALS_API_KEY": ""}):
        result = run(m.tools["wp_intervals_connection_test"]())
    assert result["connected"] is False

def test_plan_export_preserves_original():
    async def tp_get(url):
        if url.endswith("/plans/55"):
            return {"planId": 55, "startDate": "2026-01-01", "endDate": "2026-01-07"}
        return [{"workoutId": 77, "title": "Long ride", "structure": {"steps": [1,2]}}]
    m = FakeMCP()
    register_migration_tools(m, tp_get)
    data = run(m.tools["wp_export_plan_json"](55))
    assert data["count"] == 1
    assert data["workouts"][0]["structure"]["steps"] == [1,2]
