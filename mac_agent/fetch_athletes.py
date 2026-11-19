"""Fetch the current athlete roster from the existing authenticated Railway MCP.

Requires: pip install -r requirements.txt
Prompts for MCP URL without echoing the secret in Terminal.
Writes a local private manifest, never commits personal information.
"""
import asyncio
import getpass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

def unpack(result):
    obj = getattr(result, "structuredContent", None)
    if isinstance(obj, dict):
        if "text" in obj and isinstance(obj["text"], str): return json.loads(obj["text"])
        if "result" in obj and isinstance(obj["result"], dict): return obj["result"]
        return obj
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if text:
            value = json.loads(text)
            if isinstance(value, dict) and isinstance(value.get("text"), str): return json.loads(value["text"])
            if isinstance(value, dict): return value
    raise RuntimeError("Unexpected MCP response; no JSON payload")

async def main():
    url = getpass.getpass("Railway MCP URL (hidden): ").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.path.startswith("/mcp/"):
        raise ValueError("Expected full HTTPS URL ending in /mcp/<secret>")
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            athletes = unpack(await session.call_tool("tp_list_athletes", {}))
            groups = unpack(await session.call_tool("tp_list_groups", {}))
    roster = athletes.get("athletes", [])
    raw_groups = groups.get("groups", [])
    membership = {}
    for group in raw_groups:
        for athlete in group.get("athletes", []):
            aid = athlete.get("athlete_id")
            if aid is not None:
                membership.setdefault(str(aid), []).append(group.get("name"))
    rows = []
    for athlete in roster:
        aid = athlete.get("athlete_id")
        if aid is None: continue
        memberships = membership.get(str(aid), [])
        rows.append({
            "athlete_id": aid, "name": athlete.get("name"),
            "email": athlete.get("email"), "group": ", ".join(memberships) or None,
            "plan_active": None,
            "administrative_status": "paused_group" if any("PAUSAD" in (x or "").upper() for x in memberships) else "unknown",
        })
    output = Path.home() / "WOLFPACK-DEV" / "manifests" / "atletas_trainingpeaks.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"athletes": rows}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    os.chmod(output, 0o600)
    print(f"Manifest saved locally: {output}; athlete count: {len(rows)}")
    print("Plan status is unverified; no athlete folders were changed.")

if __name__ == "__main__":
    asyncio.run(main())
