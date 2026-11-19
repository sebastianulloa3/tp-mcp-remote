"""Local, conservative iCloud writer for WOLFPACK athlete exports.

Consumes a JSON manifest produced by an authenticated upstream process.
Never talks directly to TrainingPeaks and never overwrites user documents.
"""
import argparse
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

MANAGED = {"perfil.json", "resumen_atleta.md"}
def safe_name(name):
    value = unicodedata.normalize("NFKC", str(name or "Atleta"))
    value = re.sub(r"[/\\:\x00-\x1f]+", " ", value)
    return re.sub(r"\s+", " ", value).strip(" .")[:80] or "Atleta"

def category(a):
    group = str(a.get("group") or "").upper()
    if "ENTRENADOR" in group: return "04_ENTRENADORES"
    if "PAUSAD" in group: return "03_PAUSADOS"
    # Calendar workouts do not prove an assigned plan.
    if a.get("plan_active") is True: return "01_ACTIVOS"
    return "02_SIN_PLAN"  # legacy display only; unknown is NOT confirmed no-plan

def sync(manifest, root, dry_run=True):
    root = Path(root).expanduser().resolve()
    if not root.is_dir(): raise ValueError("The iCloud HUB directory must exist")
    seen, operations = set(), []
    for athlete in manifest["athletes"]:
        aid = str(int(athlete["athlete_id"]))
        if aid in seen: continue
        seen.add(aid)
        # Human-readable names; stable TP ID retained in profile.json.
        base = root / "ATLETAS" / "EXPEDIENTES"
        label = safe_name(athlete.get("name"))
        folder = base / label
        legacy = base / ("TP-" + aid)
        if legacy.exists():
            folder = legacy  # migrate separately; never duplicate existing data
        elif folder.exists():
            existing = folder / "perfil.json"
            if existing.is_file():
                try:
                    current_id = str(json.loads(existing.read_text(encoding="utf-8")).get("athlete_id"))
                except (ValueError, OSError):
                    current_id = None
                if current_id != aid:
                    folder = base / (label + " · TP-" + aid)
            else:
                folder = base / (label + " · TP-" + aid)
        profile = {
            "schema_version": 1, "source": "TrainingPeaks",
            "athlete_id": int(aid), "name": athlete.get("name"),
            "email": athlete.get("email"), "group": athlete.get("group"),
            "plan_active": athlete.get("plan_active"),
            "plan_status": "verified" if athlete.get("plan_active") is not None else "pending_verification",
            "administrative_status": athlete.get("administrative_status", "unknown"),
            "last_sync_utc": datetime.now(timezone.utc).isoformat()
        }
        summary = ("# " + str(profile["name"] or "Atleta") + "\n\n"
                   + "- TrainingPeaks ID: " + aid + "\n"
                   + "- Grupo: " + str(profile["group"] or "Sin verificar") + "\n"
                   + "- Estado del plan: " + str(profile["plan_status"]) + "\n"
                   + "- Estado administrativo: " + str(profile["administrative_status"]) + "\n"
                   + "- Historial de carreras: pendiente de extracción y revisión\n")
        operations.append({"id": aid, "path": str(folder), "classification": category(athlete)})
        if dry_run: continue
        folder.mkdir(parents=True, exist_ok=True)
        for filename, body in (("perfil.json", json.dumps(profile, ensure_ascii=False, indent=2)+"\n"),
                               ("resumen_atleta.md", summary)):
            dest = folder / filename
            if dest.is_symlink(): raise ValueError("Refusing to follow symlink: "+str(dest))
            # Only overwrite files previously marked as agent-owned.
            marker = folder / ".wolfpack-managed"
            if dest.exists() and not marker.exists():
                raise ValueError("Existing unmanaged file: "+str(dest))
            temp = folder / (filename + ".tmp")
            temp.write_text(body, encoding="utf-8")
            os.replace(temp, dest)
        (folder / ".wolfpack-managed").touch(exist_ok=True)
    return operations

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--hub", required=True)
    p.add_argument("--apply", action="store_true", help="Write agent-owned files; default is dry-run")
    args = p.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    print(json.dumps(sync(manifest, args.hub, dry_run=not args.apply), ensure_ascii=False, indent=2))
