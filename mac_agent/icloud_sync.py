"""Conservative, idempotent athlete dossier synchronization into iCloud.

Only managed profile/summary files are written. Dry-run is the default.
"""
import argparse
import json
import os
import re
import shutil
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

MANAGED = ("perfil.json", "resumen_atleta.md")
MARKER = ".wolfpack-managed"


def safe_name(name):
    value = unicodedata.normalize("NFKC", str(name or "Atleta"))
    value = re.sub(r"[/\\:\x00-\x1f]+", " ", value)
    return re.sub(r"\s+", " ", value).strip(" .")[:80] or "Atleta"


def category(a):
    group = str(a.get("group") or "").upper()
    if "ENTRENADOR" in group:
        return "04_ENTRENADORES"
    if "PAUSAD" in group:
        return "03_PAUSADOS"
    if a.get("plan_active") is True:
        return "01_ACTIVOS"
    return "PENDIENTE_VERIFICACION"


def _existing_id(folder):
    path = folder / "perfil.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("athlete_id"))
    except (ValueError, OSError):
        return None


def _folder_for(base, label, aid, duplicated):
    legacy = base / ("TP-" + aid)
    if legacy.exists():
        if legacy.is_symlink() or not legacy.is_dir() or _existing_id(legacy) != aid:
            raise ValueError("Conflicting legacy folder: " + str(legacy))
        return legacy
    # Prefer existing same-ID folders, including earlier name-based folders.
    matches = [p for p in base.iterdir() if p.is_dir() and not p.is_symlink() and _existing_id(p) == aid]
    if len(matches) > 1:
        raise ValueError("Multiple existing dossiers for TP ID " + aid)
    if matches:
        return matches[0]
    folder = base / (label + " · TP-" + aid if duplicated else label)
    if folder.exists() or folder.is_symlink():
        if _existing_id(folder) == aid and folder.is_dir() and not folder.is_symlink():
            return folder
        folder = base / (label + " · TP-" + aid)
        if folder.exists() or folder.is_symlink():
            if _existing_id(folder) != aid or folder.is_symlink() or not folder.is_dir():
                raise ValueError("Conflicting destination: " + str(folder))
    return folder


def sync(manifest, root, dry_run=True):
    root = Path(root).expanduser().resolve()
    base = root / "ATLETAS" / "EXPEDIENTES"
    if not base.is_dir() or base.is_symlink():
        raise ValueError("Existing iCloud ATLETAS/EXPEDIENTES directory required")
    athletes = manifest.get("athletes")
    if not isinstance(athletes, list):
        raise ValueError("Manifest requires an athletes list")
    ids = set()
    names = Counter(safe_name(a.get("name")).casefold() for a in athletes)
    operations = []
    planned = set()
    for athlete in athletes:
        aid = str(int(athlete["athlete_id"]))
        if aid in ids:
            raise ValueError("Duplicate TrainingPeaks ID in manifest: " + aid)
        ids.add(aid)
        label = safe_name(athlete.get("name"))
        folder = _folder_for(base, label, aid, names[label.casefold()] > 1)
        if folder in planned:
            raise ValueError("Two athletes resolve to one folder: " + str(folder))
        planned.add(folder)
        if folder.is_symlink():
            raise ValueError("Symlink destination refused: " + str(folder))
        marker = folder / MARKER
        if marker.is_symlink():
            raise ValueError("Symlink marker refused: " + str(marker))
        for filename in MANAGED:
            dest = folder / filename
            if dest.is_symlink():
                raise ValueError("Symlink file refused: " + str(dest))
            if dest.exists() and not marker.is_file():
                raise ValueError("Existing unmanaged file: " + str(dest))
        profile = {
            "schema_version": 1, "source": "TrainingPeaks",
            "athlete_id": int(aid), "name": athlete.get("name"),
            "email": athlete.get("email"), "group": athlete.get("group"),
            "plan_active": athlete.get("plan_active"),
            "plan_status": "verified" if athlete.get("plan_active") is not None else "pending_verification",
            "administrative_status": athlete.get("administrative_status", "unknown"),
            "last_sync_utc": datetime.now(timezone.utc).isoformat(),
        }
        summary = (
            "# " + str(profile["name"] or "Atleta") + "\n\n"
            + "- TrainingPeaks ID: " + aid + "\n"
            + "- Grupo: " + str(profile["group"] or "Sin verificar") + "\n"
            + "- Estado del plan: " + profile["plan_status"] + "\n"
            + "- Estado administrativo: " + str(profile["administrative_status"]) + "\n"
            + "- Historial de carreras: pendiente de extracción y revisión\n"
        )
        payloads = {"perfil.json": json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                    "resumen_atleta.md": summary}
        existing_files = [n for n in MANAGED if (folder / n).exists()]
        operations.append({"id": aid, "path": str(folder),
                           "classification": category(athlete),
                           "action": "update" if existing_files else "create",
                           "backup_required": bool(existing_files)})
        if dry_run:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        # Back up existing managed files before any replacement.
        if existing_files:
            backup = folder / ".wolfpack-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup.mkdir(parents=True, exist_ok=False)
            for filename in existing_files:
                shutil.copy2(folder / filename, backup / filename)
        for filename, body in payloads.items():
            temp = folder / (filename + ".tmp")
            if temp.exists() or temp.is_symlink():
                raise ValueError("Temporary file already exists: " + str(temp))
            with temp.open("x", encoding="utf-8") as handle:
                handle.write(body)
            os.replace(temp, folder / filename)
        marker.touch(exist_ok=True)
    return operations


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--hub", required=True)
    p.add_argument("--apply", action="store_true", help="Write managed files; default is dry-run")
    args = p.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    print(json.dumps(sync(manifest, args.hub, dry_run=not args.apply), ensure_ascii=False, indent=2))
