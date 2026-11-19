"""Create a non-destructive WOLFPACK dossier structure for existing athletes.

Dry-run by default. --apply creates missing folders and one README per dossier.
Does not modify perfil.json, resumen_atleta.md or any existing file.
"""
import argparse
import json
from pathlib import Path

SECTIONS = (
    "01_IDENTIDAD_Y_CONTACTO",
    "02_HISTORIAL_DEPORTIVO",
    "03_OBJETIVOS_Y_CALENDARIO",
    "04_PLANIFICACION",
    "05_SEGUIMIENTO_ENTRENAMIENTOS",
    "06_RECUPERACION_Y_HABITOS",
    "07_NUTRICION_Y_COMPOSICION_CORPORAL",
    "08_EVALUACIONES_Y_PRUEBAS",
    "09_COMUNICACIONES_Y_ACUERDOS",
    "10_DOCUMENTOS_COMPARTIBLES",
)
README = """# Expediente deportivo WOLFPACK

Este expediente usa `perfil.json` como identidad vinculada a TrainingPeaks.
No dupliques ni edites manualmente los archivos administrados por el sincronizador.

## Contenido
- 01 Identidad y contacto: datos no sensibles y consentimientos administrativos.
- 02 Historial deportivo: disciplinas, marcas, carreras y experiencia.
- 03 Objetivos y calendario: temporada 2027, eventos A/B/C.
- 04 Planificación: zonas comprensibles, sesiones y revisiones del entrenador.
- 05 Seguimiento: cumplimiento, carga, check-ins y alertas operativas.
- 06 Recuperación y hábitos: sueño, estrés y percepción de esfuerzo.
- 07 Nutrición y composición corporal: objetivos y citas pendientes; NO guardar aquí resultados clínicos.
- 08 Evaluaciones y pruebas: solicitudes y estado de cita con deportólogo, InBody con Dra. Vanessa y ortopedista si procede; NO guardar informes médicos.
- 09 Comunicaciones y acuerdos: notas autorizadas de staff y decisiones.
- 10 Documentos compartibles: archivos aptos para acceso operativo.

## Privacidad
La carpeta iCloud NO constituye por sí misma un repositorio clínico con
control granular de acceso. No almacenar aquí diagnósticos, resultados de
laboratorio, ECG, InBody, historia clínica ni otros datos sensibles.
Esos documentos requieren consentimiento explícito, almacenamiento restringido,
roles de acceso y políticas de retención antes de incorporarse a Wolfy.
Los estados de cita son pendientes, nunca se infiere que una evaluación ocurrió.

## Fuentes de verdad
TrainingPeaks: identidad deportiva y entrenamientos cuando estén verificados.
MANADA SITE: reservas y asistencia cuando se integre.
Wolfy: seguimiento y propuestas sujetas a aprobación del entrenador.
"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    base = Path(args.hub).expanduser() / "ATLETAS" / "EXPEDIENTES"
    if not base.is_dir() or base.is_symlink():
        raise SystemExit("No existe una carpeta de expedientes segura")
    dossiers = sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink() and (p / "perfil.json").is_file())
    ids = set()
    actions = []
    for folder in dossiers:
        profile_path = folder / "perfil.json"
        if profile_path.is_symlink():
            raise SystemExit("Perfil enlazado no permitido: " + str(folder))
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        aid = str(profile.get("athlete_id"))
        if aid in ids or aid in ("None", ""):
            raise SystemExit("ID duplicado o ausente: " + aid)
        ids.add(aid)
        for section in SECTIONS:
            target = folder / section
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise SystemExit("Conflicto: " + str(target))
            if not target.exists():
                actions.append({"athlete_id": aid, "operation": "mkdir", "path": str(target)})
        readme = folder / "LEEME_EXPEDIENTE.md"
        if readme.is_symlink() or (readme.exists() and not readme.is_file()):
            raise SystemExit("Conflicto: " + str(readme))
        if not readme.exists():
            actions.append({"athlete_id": aid, "operation": "create_readme", "path": str(readme)})
    # Full preflight above completes before any writes.
    if args.apply:
        for a in actions:
            path = Path(a["path"])
            if a["operation"] == "mkdir":
                path.mkdir(exist_ok=False)
            else:
                with path.open("x", encoding="utf-8") as out:
                    out.write(README)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run",
                      "dossiers": len(dossiers), "planned_changes": len(actions),
                      "folders": sum(a["operation"] == "mkdir" for a in actions),
                      "readmes": sum(a["operation"] == "create_readme" for a in actions),
                      "sample": actions[:3]}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
