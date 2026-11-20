"""One-shot read-only diagnostic for TrainingPeaks athlete settings.

Run only in an environment with TP_USERNAME and TP_PASSWORD already set.
Never prints tokens, cookies, credentials, or the full API response.
"""
import asyncio
import json
import sys

from server import TPClient

ATHLETE_ID = 6664356
PATH = f"/fitness/v1/athletes/{ATHLETE_ID}/settings"
INTERESTING = ("zone", "heart", "threshold", "run", "sport", "pace", "bpm", "lactate")


def summarize(obj, depth=0):
    if depth > 5:
        return "<nested>"
    if isinstance(obj, dict):
        return {str(k): summarize(v, depth + 1)
                for k, v in obj.items()
                if depth == 0 or any(s in str(k).lower() for s in INTERESTING)}
    if isinstance(obj, list):
        return {"count": len(obj), "sample": [summarize(v, depth + 1) for v in obj[:2]]}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj if isinstance(obj, (int, float, bool)) or obj is None else f"<{type(obj).__name__}>"
    return f"<{type(obj).__name__}>"


async def main():
    try:
        response = await TPClient()._request("GET", PATH)
        print("endpoint:", PATH)
        print("http_status:", response.status_code)
        if response.status_code != 200:
            print("result: endpoint not accessible with current session")
            return 2
        try:
            data = response.json()
        except ValueError:
            print("result: non-JSON response")
            return 3
        print("schema:", json.dumps(summarize(data), ensure_ascii=False, indent=2)[:6000])
        print("result: inspect schema and map to normalized zones before enabling")
        return 0
    except Exception as exc:
        print("result: request failed")
        print("error_type:", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
