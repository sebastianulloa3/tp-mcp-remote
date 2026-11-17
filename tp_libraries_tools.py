"""
Herramientas de SOLO LECTURA para TP Coach New: librerías y planes de TrainingPeaks,
para migrarlos a Intervals.icu.

Endpoints copiados de wolfpack-tp/tp_api.py (verificados en vivo el 2026-10-15
con la cuenta sebascoach3).

Cómo integrarlo en el servidor de TP Coach New (el que corre en Railway):
1. Copiar este archivo junto a su server.py.
2. En server.py, después de crear `mcp`:
       from tp_libraries_tools import register_library_tools
       register_library_tools(mcp, tp_get)
   donde `tp_get(url) -> dict|list` es la función (async o normal) que el servidor
   ya usa para hacer GET autenticados (el mismo token que usan tp_get_workouts y
   tp_list_groups). Recibe la URL completa.
3. Redeploy en Railway y "Actualizar herramientas" del conector en claude.ai.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from typing import Any

TP = "https://tpapi.trainingpeaks.com"
RX = "https://api.peakswaresb.com/rx/activity/v1"  # Strength Builder (fuerza)

NOMBRE_DEPORTE = {1: "natacion", 2: "ciclismo", 3: "carrera", 4: "brick", 5: "crosstraining",
                  6: "competencia", 7: "descanso", 8: "mtb", 9: "fuerza", 10: "custom",
                  11: "esqui", 12: "remo", 13: "caminata", 100: "otro"}


def register_library_tools(mcp, tp_get):

    async def get(url: str) -> Any:
        r = tp_get(url)
        return await r if inspect.isawaitable(r) else r

    async def me() -> int:
        return (await get(f"{TP}/users/v3/user"))["user"]["personId"]

    def _min(h):
        return round(h * 60) if h else None

    def _cardio(it: dict, estructura: bool) -> dict:
        out = {"item_id": it.get("exerciseLibraryItemId"), "tipo": "cardio",
               "titulo": it.get("itemName"),
               "deporte": NOMBRE_DEPORTE.get(it.get("workoutTypeId"), it.get("workoutTypeId")),
               "duracion_min": _min(it.get("totalTimePlanned")), "distancia_m": it.get("distancePlanned"),
               "tss": it.get("tssPlanned"), "if": it.get("ifPlanned"),
               "descripcion": it.get("description"), "comentarios_coach": it.get("coachComments")}
        if estructura:
            out["estructura"] = it.get("structure")  # JSON de pasos/repeticiones/objetivos
        return out

    def _fuerza(it: dict) -> dict:
        w = it.get("workout") or {}
        return {"item_id": it.get("id"), "tipo": "fuerza", "titulo": it.get("title"),
                "instrucciones": w.get("instructions"),
                "series": (w.get("snapshot") or {}).get("totalSets"),
                "bloques": [{"tipo": b.get("blockType"), "titulo": b.get("title"),
                             "ejercicios": [{"nombre": (p.get("exercise") or {}).get("title"),
                                             "series": len(p.get("sets") or [])}
                                            for p in b.get("prescriptions") or []]}
                            for b in w.get("blocks") or []]}

    @mcp.tool()
    async def tp_list_libraries(solo_propias: bool = True) -> dict:
        """Lista las carpetas de la biblioteca de entrenamientos del coach
        (id, nombre, si es propia). Solo lectura."""
        yo = await me()
        libs = await get(f"{TP}/exerciselibrary/v2/libraries") or []
        out = [{"library_id": c["exerciseLibraryId"], "nombre": c["libraryName"],
                "propia": c.get("ownerId") == yo} for c in libs]
        if solo_propias:
            out = [c for c in out if c["propia"]]
        return {"count": len(out), "libraries": out}

    @mcp.tool()
    async def tp_get_library_workouts(library_id: int, incluir_estructura: bool = True,
                                      incluir_fuerza: bool = True, offset: int = 0,
                                      limit: int = 40) -> dict:
        """Devuelve las sesiones de una carpeta de la biblioteca: título, deporte,
        duración, TSS, descripción, comentarios y estructura de intervalos (JSON).
        Incluye las de fuerza (Strength Builder) con bloques y ejercicios.
        Pagina con offset/limit (máx. 40). Solo lectura."""
        limit = max(1, min(limit, 40))
        items = [_cardio(it, incluir_estructura)
                 for it in (await get(f"{TP}/exerciselibrary/v2/libraries/{library_id}/items") or [])]
        if incluir_fuerza:
            try:
                r = await get(f"{RX}/libraryContent/workoutLibrary/{library_id}")
                items += [_fuerza(it) for it in ((r or {}).get("data", r) or [])]
            except Exception:
                pass  # la carpeta no tiene fuerza
        page = items[offset: offset + limit]
        return {"library_id": library_id, "total": len(items), "offset": offset,
                "next_offset": offset + limit if offset + limit < len(items) else None,
                "workouts": page}

    @mcp.tool()
    async def tp_list_plans(texto: str | None = None) -> dict:
        """Lista los planes de entrenamiento del coach (id, título, semanas,
        sesiones, si es dinámico). Solo lectura."""
        out = [{"plan_id": p["planId"], "titulo": p.get("title"), "semanas": p.get("weekCount"),
                "sesiones": p.get("workoutCount"), "inicio": (p.get("startDate") or "")[:10] or None,
                "dinamico": p.get("isDynamic")} for p in (await get(f"{TP}/plans/v1/plans") or [])]
        if texto:
            out = [p for p in out if texto.lower() in (p["titulo"] or "").lower()]
        return {"count": len(out), "plans": out}

    @mcp.tool()
    async def tp_get_plan_workouts(plan_id: int, incluir_estructura: bool = True,
                                   offset: int = 0, limit: int = 40) -> dict:
        """Devuelve las sesiones de un plan ordenadas por fecha, con día del plan,
        título, deporte, duración, TSS, descripción y estructura (JSON).
        Pagina con offset/limit (máx. 40). Solo lectura."""
        p = await get(f"{TP}/plans/v1/plans/{plan_id}")
        d = (p.get("startDate") or "2020-01-01")[:10]
        h = (p.get("endDate") or (date.today() + timedelta(days=800)).isoformat())[:10]
        ws = sorted(await get(f"{TP}/plans/v1/plans/{plan_id}/workouts/{d}/{h}") or [],
                    key=lambda w: w.get("workoutDay") or "")
        inicio = date.fromisoformat(d)
        res = []
        for w in ws:
            dia = (w.get("workoutDay") or "")[:10]
            item = {"workout_id": w.get("workoutId"), "fecha": dia,
                    "dia_del_plan": (date.fromisoformat(dia) - inicio).days + 1 if dia else None,
                    "titulo": w.get("title"),
                    "deporte": NOMBRE_DEPORTE.get(w.get("workoutTypeValueId"), w.get("workoutTypeValueId")),
                    "duracion_min": _min(w.get("totalTimePlanned")), "tss": w.get("tssPlanned"),
                    "descripcion": w.get("description"), "comentarios_coach": w.get("coachComments")}
            if incluir_estructura:
                item["estructura"] = w.get("structure")
            res.append(item)
        limit = max(1, min(limit, 40))
        return {"plan_id": plan_id, "titulo": p.get("title"), "dinamico": p.get("isDynamic"),
                "total": len(res), "offset": offset,
                "next_offset": offset + limit if offset + limit < len(res) else None,
                "workouts": res[offset: offset + limit]}
