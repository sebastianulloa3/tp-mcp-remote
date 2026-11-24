"""Traductor TrainingPeaks -> Intervals.icu (solo lectura, sin red).

Convierte la estructura de una sesión de TrainingPeaks al texto de
entrenamiento de Intervals.icu. No llama a ninguna API ni escribe datos:
devuelve un borrador para revisión humana antes de cualquier carga.
"""
from collections import Counter

# Métrica de intensidad de TP -> sufijo de Intervals
METRIC_SUFFIX = {
    "percentOfThresholdPace": "Pace",
    "percentOfThresholdHr": "LTHR",
    "percentOfFtp": "",  # en Intervals "75%" a secas es % de FTP
}
SPORT_TYPE = {1: "Swim", 2: "Ride", 3: "Run", 9: "WeightTraining"}


class Unsupported(Exception):
    """Estructura que el traductor todavía no sabe convertir."""


def _num(v):
    return f"{v:g}"


def format_length(length):
    value, unit = length["value"], length["unit"]
    if unit == "meter":
        return f"{_num(value)}mtr"  # en Intervals "m" son minutos
    if unit == "kilometer":
        return f"{_num(value)}km"
    if unit == "second":
        value = int(value)
        return f"{value // 60}m" if value >= 60 and value % 60 == 0 else f"{value}s"
    if unit == "minute":
        return f"{_num(value)}m"
    raise Unsupported(f"unidad de longitud no soportada: {unit}")


def format_target(targets, metric):
    if not targets:
        return ""
    if metric not in METRIC_SUFFIX:
        raise Unsupported(f"métrica de intensidad no soportada: {metric}")
    lo, hi = targets[0].get("minValue"), targets[0].get("maxValue")
    rng = _num(lo) if hi is None or lo == hi else f"{_num(lo)}-{_num(hi)}"
    suffix = METRIC_SUFFIX[metric]
    return f"{rng}% {suffix}" if suffix else f"{rng}%"


def format_step(step, metric):
    if step.get("openDuration"):
        raise Unsupported("paso de duración abierta")
    targets = step.get("targets")
    is_rest = step.get("intensityClass") == "rest"
    # Descanso "0-77%" en TP no es un objetivo real: va sin objetivo
    if is_rest and targets and not targets[0].get("minValue"):
        targets = None
    parts = ["-", format_length(step["length"]), format_target(targets, metric)]
    if is_rest:
        parts.append("intensity=rest")
    name = step.get("name")
    if name and name not in ("Difícil", "Fácil", "Activo"):
        parts.append(name)
    return " ".join(p for p in parts if p)


def translate_structure(structure):
    """Devuelve el texto de Intervals para una estructura de TP."""
    metric = structure.get("primaryIntensityMetric")
    blocks = []
    for block in structure.get("structure", []):
        length = block.get("length", {})
        reps = int(length.get("value", 1)) if length.get("unit") == "repetition" else 1
        lines = []
        for step in block.get("steps", []):
            if "steps" in step:
                raise Unsupported("repeticiones anidadas")
            lines.append(format_step(step, metric))
        if block.get("type") == "repetition" or reps > 1:
            blocks.append(f"{reps}x\n" + "\n".join(lines))
        else:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def structure_total(structure):
    """Total de la estructura (metros si la longitud principal es distancia)."""
    blocks = structure.get("structure") or []
    return blocks[-1].get("end") if blocks else None


def translate_workouts(workouts):
    """Traduce una lista de sesiones exportadas (wp_export_library_json).

    Siempre es un borrador: write_performed=False en todos los ítems.
    Marca títulos repetidos para que el coach decida cuál migrar.
    """
    titles = Counter(w.get("title") for w in workouts)
    items = []
    for w in workouts:
        item = {
            "source_id": w.get("source_id"),
            "title": w.get("title"),
            "type": SPORT_TYPE.get(w.get("sport_id"), f"sport_{w.get('sport_id')}"),
            "duplicate_title": titles[w.get("title")] > 1,
            "intervals_description": None,
            "notes": w.get("description"),
            "coach_comments": w.get("coach_comments"),
            "write_performed": False,
        }
        structure = w.get("structure")
        if not structure:
            item["status"] = "solo_texto"
        else:
            try:
                item["intervals_description"] = translate_structure(structure)
                item["structure_total"] = structure_total(structure)
                item["status"] = "traducido"
            except Unsupported as exc:
                item["status"] = f"no_soportado: {exc}"
        items.append(item)
    return {
        "dry_run": True,
        "count": len(items),
        "summary": dict(Counter(i["status"] for i in items)),
        "items": items,
    }
