"""
TrainingPeaks MCP — versión REMOTA (lectura + edición para coach)
=================================================================

Un servidor MCP mínimo que conecta TrainingPeaks con Claude (y cualquier
asistente compatible con el Model Context Protocol).

Lectura: perfil, atletas, carpetas del coach, entrenamientos, forma, resumen
semanal y revisión de cargas por carpeta.
Edición (solo si la variable MCP_SECRET está configurada): crear, editar,
mover, ocultar/liberar y borrar sesiones planificadas.

Seguridad: si MCP_SECRET existe, el endpoint pasa a ser /mcp/<MCP_SECRET> y
/mcp a secas deja de responder. Sin MCP_SECRET el servidor queda en solo lectura.

Expone estas herramientas:
  - tp_get_profile          -> tu perfil de atleta
  - tp_list_athletes        -> atletas disponibles (en cuenta de coach)
  - tp_get_workouts         -> entrenamientos en un rango de fechas
  - tp_get_fitness          -> curva de forma/fatiga (CTL / ATL / TSB)
  - tp_get_weekly_summary   -> resumen de una semana
  - tp_list_groups          -> carpetas del coach (A Lunes, Packs...) con sus atletas
  - tp_review_week          -> por carpeta: quién no tiene carga, quién tiene
                               sesiones ocultas y quién está en rojo
  - tp_create_workout / tp_update_workout / tp_delete_workout
  - tp_release_week         -> libera (hace visibles) las sesiones ocultas de la semana

Autenticación: login automático con tu usuario y contraseña de TrainingPeaks.
Las credenciales viven en las variables de entorno del servidor (TP_USERNAME,
TP_PASSWORD) y solo se envían directamente a TrainingPeaks por HTTPS.

Transporte: HTTP (streamable-http) para poder hospedarlo en la nube y usarlo
como conector "Web" en Claude desde cualquier dispositivo. Endpoint: /mcp
"""

import os
import sys
import time
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any
from html.parser import HTMLParser

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging: TODO va a stderr.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("tp-remote")

# Base de la API interna que usa la web de TrainingPeaks.
TP_API_BASE = "https://tpapi.trainingpeaks.com"


SPORTS: dict[str, int] = {
    "natacion": 1, "swim": 1, "ciclismo": 2, "bike": 2, "carrera": 3, "run": 3,
    "running": 3, "brick": 4, "crosstrain": 5, "carrera_competencia": 6, "race": 6,
    "descanso": 7, "dayoff": 7, "mtb": 8, "fuerza": 9, "strength": 9, "custom": 10,
    "esqui": 11, "remo": 12, "caminata": 13, "walk": 13, "otro": 100, "other": 100,
}
SPORT_NAMES: dict[int, str] = {
    1: "Natación", 2: "Ciclismo", 3: "Carrera", 4: "Brick", 5: "Crosstrain",
    6: "Competencia", 7: "Descanso", 8: "MTB", 9: "Fuerza", 10: "Custom",
    11: "Esquí", 12: "Remo", 13: "Caminata", 100: "Otro",
}


def _sport_id(sport: str) -> int:
    key = sport.strip().lower().replace("ó", "o").replace("í", "i").replace(" ", "_")
    if key not in SPORTS:
        raise ValueError(f"Deporte '{sport}' no reconocido. Usa: natacion, ciclismo, carrera, brick, fuerza, descanso, otro.")
    return SPORTS[key]


def _week_bounds(week_of: str | None) -> tuple[date, date]:
    ref = datetime.strptime(week_of, "%Y-%m-%d").date() if week_of else date.today()
    monday = ref - timedelta(days=ref.weekday())
    return monday, monday + timedelta(days=6)


def _compliance(w: dict[str, Any], today: date) -> str:
    """Color de cumplimiento al estilo TrainingPeaks."""
    day = datetime.strptime(w["date"], "%Y-%m-%d").date() if w.get("date") else today
    planned = w.get("duration_hours_planned")
    actual = w.get("duration_hours_actual")
    if w["status"] == "completado":
        if not planned:
            return "sin plan (hecho)"
        r = (actual or 0) / planned
        if r < 0.5 or r > 1.5:
            return "rojo"
        if r < 0.8 or r > 1.2:
            return "amarillo"
        return "verde"
    if day < today:
        return "rojo"  # planificado, día pasado, no se hizo
    return "pendiente"


def _writes_enabled() -> None:
    if not os.environ.get("MCP_SECRET"):
        raise PermissionError(
            "La edición está desactivada: falta la variable MCP_SECRET en Railway. "
            "Configúrala y usa la URL /mcp/<MCP_SECRET> en el conector."
        )


# ---------------------------------------------------------------------------
# PASO 1 — El login (la parte "difícil que funciona")
# ---------------------------------------------------------------------------
class _LoginFormParser(HTMLParser):
    """Extrae el __RequestVerificationToken oculto del formulario de login."""

    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.verification_token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "form" and attrs_dict.get("name") == "login-form":
            self.in_form = True
        if self.in_form and tag == "input":
            if attrs_dict.get("name") == "__RequestVerificationToken":
                self.verification_token = attrs_dict.get("value")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.in_form = False


class TPClient:
    """Cliente ligero y de solo lectura para la API de TrainingPeaks."""

    _token: str | None = None
    _token_expiry: float = 0.0
    _user: dict[str, Any] | None = None

    async def _login_and_get_cookie(self) -> str:
        """Inicia sesión con usuario/contraseña y devuelve la cookie de sesión."""
        username = os.environ.get("TP_USERNAME")
        password = os.environ.get("TP_PASSWORD")
        if not username or not password:
            raise ValueError(
                "Faltan credenciales. Define TP_USERNAME y TP_PASSWORD "
                "en las variables de entorno del servidor."
            )

        logger.info("Iniciando sesión en TrainingPeaks como '%s'...", username)
        login_url = "https://home.trainingpeaks.com/login"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Origin": "https://home.trainingpeaks.com",
            "Referer": "https://home.trainingpeaks.com/login",
        }

        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20.0) as client:
            get_resp = await client.get(login_url)
            if get_resp.status_code != 200:
                raise ValueError(f"No se pudo cargar la pagina de login: HTTP {get_resp.status_code}")

            parser = _LoginFormParser()
            parser.feed(get_resp.text)
            token = parser.verification_token
            if not token:
                raise ValueError("No se encontro el __RequestVerificationToken en la pagina de login.")

            data = {
                "__RequestVerificationToken": token,
                "CaptchaHidden": "true",
                "CaptchaToken": "",
                "Attempts": "",
                "SelectedMfaMethod": "",
                "Username": username,
                "Password": password,
            }
            post_resp = await client.post(login_url, data=data)

            # --- DIAGNOSTICO REAL --------------------------------------------
            cookie_names = list(client.cookies.keys())
            logger.info(
                "POST /login -> HTTP %s | url final: %s | cookies: %s",
                post_resp.status_code,
                post_resp.url,
                cookie_names or "(ninguna)",
            )

            tp_auth = client.cookies.get("Production_tpAuth")

            # Fallback: TP puede haber renombrado la cookie de sesion.
            if not tp_auth:
                for name in cookie_names:
                    if "tpauth" in name.lower():
                        logger.warning(
                            "Usando cookie alterna '%s' (se esperaba 'Production_tpAuth').", name
                        )
                        tp_auth = client.cookies.get(name)
                        break

            if tp_auth:
                logger.info("Login correcto.")
                return tp_auth

            txt = post_resp.text
            low = txt.lower()

            if any(k in low for k in ("selectedmfamethod", "verification code", "codigo de verificacion", "two-factor", "two factor")):
                raise ValueError(
                    "Login bloqueado por verificacion en dos pasos (MFA/2FA). "
                    "Desactiva el 2FA en esta cuenta de TrainingPeaks, o hay que implementar el flujo MFA."
                )
            if "captcha" in low:
                raise ValueError(
                    "Login bloqueado por CAPTCHA. Suele activarse tras varios intentos seguidos. "
                    "Espera 15-30 minutos sin reintentar y vuelve a probar."
                )
            if any(k in low for k in ("locked", "bloquead", "suspend")):
                raise ValueError("TrainingPeaks reporta la cuenta bloqueada o suspendida temporalmente.")
            if any(k in low for k in ("incorrect", "no es correcto", "invalid username", "invalid password")):
                raise ValueError(
                    f"TrainingPeaks rechazo las credenciales de '{username}'. "
                    f"(HTTP {post_resp.status_code}, url final: {post_resp.url})"
                )

            snippet = " ".join(txt.split())[:500]
            raise ValueError(
                f"Login fallido: no llego la cookie de sesion. "
                f"HTTP {post_resp.status_code} | url final: {post_resp.url} | "
                f"cookies: {cookie_names or '(ninguna)'} | respuesta: {snippet}"
            )

    async def _get_token(self) -> str:
        now = time.time()
        if TPClient._token and now < TPClient._token_expiry:
            return TPClient._token

        cookie = await self._login_and_get_cookie()
        headers = {"Cookie": f"Production_tpAuth={cookie.strip()}", "Accept": "application/json"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            logger.info("Canjeando la cookie por un access token...")
            resp = await client.get(f"{TP_API_BASE}/users/v3/token", headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"No se pudo obtener el token: HTTP {resp.status_code}")

            token_info = resp.json().get("token", {})
            token = token_info.get("access_token")
            if not token:
                raise ValueError("La respuesta no contenía un access_token.")

            expires_in = token_info.get("expires_in", 3600)
            TPClient._token = token
            TPClient._token_expiry = now + expires_in - 300  # 5 min de margen
            return token

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def _load_user(self) -> dict[str, Any]:
        if TPClient._user is not None:
            return TPClient._user

        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{TP_API_BASE}/users/v3/user", headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"No se pudo leer el perfil: HTTP {resp.status_code}")
            TPClient._user = resp.json().get("user", {})
            return TPClient._user

    @staticmethod
    def _full_name(d: dict[str, Any]) -> str:
        return (d.get("fullName") or f"{d.get('firstName', '')} {d.get('lastName', '')}").strip()

    async def _athletes(self) -> list[dict[str, Any]]:
        user = await self._load_user()
        result = [
            {"athlete_id": a.get("athleteId"), "name": self._full_name(a), "email": a.get("email")}
            for a in (user.get("athletes") or [])
            if a.get("athleteId")
        ]
        if user.get("isAthlete") and user.get("personId"):
            me = {"athlete_id": user.get("personId"), "name": self._full_name(user), "email": user.get("email")}
            if not any(a["athlete_id"] == me["athlete_id"] for a in result):
                result.insert(0, me)
        return result

    async def _resolve_athlete(self, athlete: str | int) -> int:
        s = str(athlete).strip()
        if s.isdigit():
            return int(s)

        athletes = await self._athletes()
        q = s.lower()
        matches = [a for a in athletes if q in a["name"].lower()]
        if not matches:
            raise ValueError(
                f"No encontré ningún atleta que coincida con '{athlete}'. "
                f"Usa tp_list_athletes para ver los nombres disponibles."
            )
        if len(matches) > 1:
            nombres = ", ".join(f"{a['name']} (id {a['athlete_id']})" for a in matches[:10])
            raise ValueError(
                f"Varios atletas coinciden con '{athlete}': {nombres}. "
                f"Especifica el nombre completo o el athleteId."
            )
        return matches[0]["athlete_id"]

    # -- Lecturas -----------------------------------------------------------

    async def list_athletes(self, query: str | None = None) -> dict[str, Any]:
        athletes = await self._athletes()
        if query:
            q = query.lower()
            athletes = [a for a in athletes if q in a["name"].lower()]
        return {"count": len(athletes), "athletes": athletes}

    async def get_profile(self) -> dict[str, Any]:
        user = await self._load_user()
        is_premium = user.get("settings", {}).get("account", {}).get("isPremium", False)
        athletes = await self._athletes()
        return {
            "user_id": user.get("personId"),
            "name": self._full_name(user),
            "email": user.get("email"),
            "account_type": "premium" if is_premium else "basic",
            "is_coach": (user.get("userType") == 2) or (not user.get("isAthlete")),
            "athlete_count": len(athletes),
        }

    async def get_workouts(self, athlete: str | int, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        if not end_date:
            end_date = date.today().isoformat()
        if not start_date:
            start_date = (date.today() - timedelta(days=7)).isoformat()
        athlete_id = await self._resolve_athlete(athlete)
        headers = await self._auth_headers()
        endpoint = f"{TP_API_BASE}/fitness/v6/athletes/{athlete_id}/workouts/{start_date}/{end_date}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(endpoint, headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"No se pudieron leer los entrenamientos: HTTP {resp.status_code}")

            workouts = resp.json() if isinstance(resp.json(), list) else []
            result = []
            for w in workouts:
                day = (w.get("workoutDay") or "").split("T")[0]
                actual = w.get("totalTime")
                completed = (w.get("completed") is True) or (actual is not None and actual > 0)
                result.append({
                    "id": str(w.get("workoutId")) if w.get("workoutId") is not None else None,
                    "date": day,
                    "title": w.get("title"),
                    "sport": SPORT_NAMES.get(w.get("workoutTypeValueId"), "Otro"),
                    "status": "completado" if completed else "planificado",
                    "hidden": bool(w.get("isHidden")),
                    "duration_hours_planned": w.get("totalTimePlanned"),
                    "duration_hours_actual": actual,
                    "tss": w.get("tssActual") if w.get("tssActual") is not None else w.get("tssPlanned"),
                    "description": w.get("description"),
                })
            return {"athlete_id": athlete_id, "count": len(result), "date_range": {"start": start_date, "end": end_date}, "workouts": result}

    async def get_fitness(self, athlete: str | int, days: int = 90, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        athlete_id = await self._resolve_athlete(athlete)
        headers = await self._auth_headers()

        if start_date and end_date:
            q_start, q_end = start_date, end_date
        else:
            today = date.today()
            q_start = (today - timedelta(days=days)).isoformat()
            q_end = today.isoformat()

        endpoint = f"{TP_API_BASE}/fitness/v1/athletes/{athlete_id}/reporting/performancedata/{q_start}/{q_end}"
        body = {"atlConstant": 7, "atlStart": 0, "ctlConstant": 42, "ctlStart": 0, "workoutTypes": []}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(endpoint, json=body, headers=headers)
            if resp.status_code != 200:
                raise ValueError(f"No se pudieron leer los datos de forma: HTTP {resp.status_code}")

            daily = []
            for entry in resp.json():
                daily.append({
                    "date": (entry.get("workoutDay") or "").split("T")[0],
                    "ctl": round(entry.get("ctl", 0), 1),
                    "atl": round(entry.get("atl", 0), 1),
                    "tsb": round(entry.get("tsb", 0), 1),
                })

            current = None
            if daily:
                tsb = daily[-1]["tsb"]
                if tsb > 25:
                    status = "Muy fresco (riesgo de desentrenamiento)"
                elif tsb > 10:
                    status = "Fresco (listo para competir)"
                elif tsb > 0:
                    status = "Neutro (entrenamiento normal)"
                elif tsb > -10:
                    status = "Cansado (asimilando carga)"
                elif tsb > -25:
                    status = "Muy cansado (fatiga alta)"
                else:
                    status = "Agotado (riesgo de sobreentrenamiento)"
                current = {**daily[-1], "estado": status}

            return {"start_date": q_start, "end_date": q_end, "current": current, "daily_data": daily}

    # -- Carpetas del coach (en la API se llaman "tags") ------------------

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        headers = await self._auth_headers()
        if "json" in kw:
            headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient(timeout=20.0) as c:
            return await c.request(method, f"{TP_API_BASE}{path}", headers=headers, **kw)

    async def list_groups(self) -> dict[str, Any]:
        user = await self._load_user()
        coach_id = user.get("personId")
        resp = await self._request("GET", f"/coaches/v2/coaches/{coach_id}/tags")
        if resp.status_code != 200:
            raise ValueError(f"No se pudieron leer las carpetas: HTTP {resp.status_code}")
        names = {a["athlete_id"]: a["name"] for a in await self._athletes()}
        groups = []
        for t in resp.json() if isinstance(resp.json(), list) else []:
            ids = t.get("athleteIds") or []
            groups.append({
                "id": t.get("id"),
                "name": t.get("name", ""),
                "is_default": t.get("isDefault", False),
                "athlete_count": len(ids),
                "athletes": [{"athlete_id": i, "name": names.get(i, f"id {i}")} for i in ids],
            })
        return {"count": len(groups), "groups": groups}

    async def review_week(self, groups: list[str] | None = None, week_of: str | None = None) -> dict[str, Any]:
        monday, sunday = _week_bounds(week_of)
        today = date.today()
        all_groups = (await self.list_groups())["groups"]
        if groups:
            wanted = [g.strip().lower() for g in groups]
            sel = [g for g in all_groups if any(w in g["name"].lower() for w in wanted)]
        else:
            sel = [g for g in all_groups if not g["is_default"]]
        if not sel:
            raise ValueError("No encontré esas carpetas. Usa tp_list_groups para ver los nombres.")

        sem = asyncio.Semaphore(6)

        async def check(a: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                try:
                    data = await self.get_workouts(a["athlete_id"], monday.isoformat(), sunday.isoformat())
                except Exception as e:  # noqa: BLE001
                    return {**a, "error": str(e)}
            ws = [w for w in data["workouts"] if w["sport"] != "Descanso"]
            for w in ws:
                w["color"] = _compliance(w, today)
            planned = [w for w in ws if w["duration_hours_planned"] or w["status"] == "planificado"]
            hidden = [w for w in ws if w["hidden"] and w["status"] == "planificado"]
            red = [w for w in ws if w["color"] == "rojo"]
            if not planned:
                estado = "SIN CARGA"
            elif len(hidden) == len([w for w in planned if w["status"] == "planificado"]) and hidden:
                estado = "CARGA OCULTA"
            elif red:
                estado = "EN ROJO"
            else:
                estado = "OK"
            return {
                **a,
                "estado": estado,
                "sesiones_planificadas": len(planned),
                "ocultas": len(hidden),
                "completadas": len([w for w in ws if w["status"] == "completado"]),
                "en_rojo": [{"fecha": w["date"], "titulo": w["title"], "deporte": w["sport"]} for w in red],
            }

        result = []
        for g in sel:
            rows = await asyncio.gather(*(check(a) for a in g["athletes"]))
            order = {"SIN CARGA": 0, "CARGA OCULTA": 1, "EN ROJO": 2, "OK": 3}
            rows = sorted(rows, key=lambda r: order.get(r.get("estado"), -1))
            result.append({
                "carpeta": g["name"],
                "resumen": {k: sum(1 for r in rows if r.get("estado") == k) for k in order},
                "atletas": rows,
            })
        return {"semana": {"inicio": monday.isoformat(), "fin": sunday.isoformat()}, "carpetas": result}

    # -- Edición de sesiones --------------------------------------------------

    async def create_workout(self, athlete: str, workout_date: str, sport: str, title: str,
                             duration_minutes: float | None = None, description: str | None = None,
                             distance_km: float | None = None, tss_planned: float | None = None,
                             hidden: bool = False) -> dict[str, Any]:
        _writes_enabled()
        athlete_id = await self._resolve_athlete(athlete)
        sid = _sport_id(sport)
        payload: dict[str, Any] = {
            "athleteId": athlete_id,
            "workoutDay": f"{workout_date}T00:00:00",
            "workoutTypeFamilyId": sid,
            "workoutTypeValueId": sid,
            "title": title,
            "isHidden": hidden,
        }
        if duration_minutes is not None:
            payload["totalTimePlanned"] = duration_minutes / 60.0
        if description:
            payload["description"] = description
        if distance_km is not None:
            payload["distancePlanned"] = distance_km * 1000
        if tss_planned is not None:
            payload["tssPlanned"] = tss_planned
        resp = await self._request("POST", f"/fitness/v6/athletes/{athlete_id}/workouts", json=payload)
        if resp.status_code not in (200, 201):
            raise ValueError(f"No se pudo crear la sesión: HTTP {resp.status_code} {resp.text[:200]}")
        d = resp.json()
        return {"ok": True, "workout_id": d.get("workoutId"), "athlete_id": athlete_id,
                "fecha": workout_date, "titulo": title, "oculta": hidden}

    async def _get_workout_raw(self, athlete_id: int, workout_id: str) -> dict[str, Any]:
        resp = await self._request("GET", f"/fitness/v6/athletes/{athlete_id}/workouts/{workout_id}")
        if resp.status_code != 200:
            raise ValueError(f"No encontré la sesión {workout_id}: HTTP {resp.status_code}")
        return resp.json()

    async def preview_copy_week(self, athlete: str, source_week: str,
                                target_week: str, sports: list[str] | None = None,
                                weekdays: list[int] | None = None) -> dict[str, Any]:
        """Previsualiza copias de sesiones planificadas sin modificar calendarios."""
        source_day = datetime.strptime(source_week, "%Y-%m-%d").date()
        target_day = datetime.strptime(target_week, "%Y-%m-%d").date()
        source_monday = source_day - timedelta(days=source_day.weekday())
        target_monday = target_day - timedelta(days=target_day.weekday())
        if source_monday == target_monday:
            raise ValueError("La semana origen y destino deben ser distintas.")
        if weekdays is not None and any(d not in range(7) for d in weekdays):
            raise ValueError("weekdays usa 0=lunes hasta 6=domingo.")
        source = await self.get_workouts(athlete, source_monday.isoformat(),
                                         (source_monday + timedelta(days=6)).isoformat())
        target = await self.get_workouts(athlete, target_monday.isoformat(),
                                         (target_monday + timedelta(days=6)).isoformat())
        selected_sports = {x.lower() for x in sports} if sports else None
        proposals = []
        for w in source["workouts"]:
            if w["sport"] == "Descanso" or w["status"] != "planificado":
                continue
            day = date.fromisoformat(w["date"])
            if weekdays is not None and day.weekday() not in weekdays:
                continue
            if selected_sports and w["sport"].lower() not in selected_sports:
                continue
            new_day = target_monday + timedelta(days=day.weekday())
            existing = [x for x in target["workouts"] if x["date"] == new_day.isoformat()
                        and x["sport"] == w["sport"]]
            proposals.append({
                "source_workout_id": w["id"], "title": w["title"],
                "sport": w["sport"], "source_date": w["date"],
                "target_date": new_day.isoformat(), "target_existing": existing,
                "conflict": bool(existing), "description": w.get("description"),
                "duration_minutes": round(w["duration_hours_planned"] * 60, 2)
                    if w.get("duration_hours_planned") is not None else None,
                "tss_planned": w.get("tss") if w["status"] == "planificado" else None,
            })
        return {"athlete_id": source["athlete_id"], "source_week": source_monday.isoformat(),
                "target_week": target_monday.isoformat(), "proposals": proposals,
                "applied": False, "note": "Solo previsualización. Requiere aprobación antes de crear copias."}

    async def preview_workout_edit(self, athlete: str, workout_id: str,
                                   tss_planned: float | None = None,
                                   coach_comment: str | None = None) -> dict[str, Any]:
        """Previsualiza cambios permitidos; nunca escribe ni altera TSS real."""
        athlete_id = await self._resolve_athlete(athlete)
        w = await self._get_workout_raw(athlete_id, workout_id)
        if str(w.get("athleteId", athlete_id)) != str(athlete_id):
            raise ValueError("La actividad no pertenece al atleta indicado.")
        if tss_planned is None and coach_comment is None:
            raise ValueError("Indica al menos un campo a previsualizar.")
        if tss_planned is not None and not (0 <= tss_planned <= 2000):
            raise ValueError("tss_planned debe estar entre 0 y 2000.")
        completed = bool(w.get("completed")) or (w.get("totalTime") or 0) > 0
        if completed and tss_planned is not None:
            raise ValueError("No se puede corregir TSS real cambiando tss_planned en una sesión completada.")
        changes = {}
        if tss_planned is not None:
            changes["tssPlanned"] = {"before": w.get("tssPlanned"), "after": tss_planned}
        if coach_comment is not None:
            changes["coachComments"] = {"before": w.get("coachComments"), "after": coach_comment}
        return {
            "athlete_id": athlete_id, "workout_id": workout_id,
            "completed": completed, "changes": changes, "applied": False,
            "next_step": "Solicitar confirmación explícita del coach y usar tp_update_workout únicamente para estos campos permitidos.",
        }

    async def audit_tss_range(self, athlete: str, start_date: str, end_date: str,
                              tss_per_hour_alert: float = 150.0) -> dict[str, Any]:
        """Señala cargas atípicas sin afirmar que sean erróneas."""
        if not 0 < tss_per_hour_alert <= 1000:
            raise ValueError("tss_per_hour_alert debe estar entre 0 y 1000.")
        data = await self.get_workouts(athlete, start_date, end_date)
        alerts = []
        for w in data["workouts"]:
            if w["status"] != "completado" or w["sport"] == "Descanso":
                continue
            hours, tss = w.get("duration_hours_actual"), w.get("tss")
            if not isinstance(hours, (int, float)) or hours <= 0:
                continue
            if not isinstance(tss, (int, float)):
                continue
            rate = tss / hours
            reasons = []
            if rate >= tss_per_hour_alert:
                reasons.append("TSS por hora elevado: comprobar método y umbrales")
            if w["sport"] == "Natación" and hours >= 0.3 and tss < 2:
                reasons.append("TSS de natación casi nulo: comprobar datos de intensidad")
            if reasons:
                alerts.append({
                    "workout_id": w["id"], "date": w["date"],
                    "title": w["title"], "sport": w["sport"],
                    "duration_hours": round(hours, 3), "tss": tss,
                    "tss_per_hour": round(rate, 1), "reasons": reasons,
                })
        return {
            "athlete_id": data["athlete_id"], "date_range": data["date_range"],
            "alerts": alerts, "alert_count": len(alerts),
            "interpretation": "Indicadores de auditoría, no diagnóstico ni corrección automática.",
        }

    async def get_workout_details(self, athlete: str, workout_id: str) -> dict[str, Any]:
        """Detalle de una actividad, solo lectura. Campos no disponibles quedan explícitos."""
        athlete_id = await self._resolve_athlete(athlete)
        w = await self._get_workout_raw(athlete_id, workout_id)
        if str(w.get("athleteId", athlete_id)) != str(athlete_id):
            raise ValueError("La actividad no corresponde al atleta solicitado.")
        keys = (
            "workoutId", "workoutDay", "title", "workoutTypeValueId",
            "completed", "totalTime", "distance", "distanceActual",
            "totalTimePlanned", "distancePlanned", "tssActual", "tssPlanned",
            "tssMethod", "tssType", "tssTypeId", "intensityFactor",
            "normalizedPower", "averagePower", "averageHeartRate", "maxHeartRate",
            "averageSpeed", "averagePace", "thresholdPace", "thresholdHeartRate",
            "thresholdPower", "pace", "speed", "elevationGain",
        )
        fields = {k: w[k] for k in keys if k in w}
        missing = [k for k in keys if k not in w]
        return {
            "athlete_id": athlete_id, "workout_id": workout_id,
            "fields": fields, "unavailable_fields": missing,
            "thresholds_verified": False,
            "note": "Los umbrales y el método de TSS no se pueden confirmar si la respuesta no los expone. No se modifica TrainingPeaks.",
        }

    async def update_workout(self, athlete: str, workout_id: str, title: str | None = None,
                             description: str | None = None, workout_date: str | None = None,
                             duration_minutes: float | None = None, distance_km: float | None = None,
                             tss_planned: float | None = None, sport: str | None = None,
                             hidden: bool | None = None, coach_comment: str | None = None) -> dict[str, Any]:
        _writes_enabled()
        athlete_id = await self._resolve_athlete(athlete)
        w = await self._get_workout_raw(athlete_id, workout_id)
        if title is not None:
            w["title"] = title
        if description is not None:
            w["description"] = description
        if workout_date is not None:
            w["workoutDay"] = f"{workout_date}T00:00:00"
            if w.get("startTimePlanned"):
                t = w["startTimePlanned"].split("T")[1] if "T" in w["startTimePlanned"] else "00:00:00"
                w["startTimePlanned"] = f"{workout_date}T{t}"
        if duration_minutes is not None:
            w["totalTimePlanned"] = duration_minutes / 60.0
        if distance_km is not None:
            w["distancePlanned"] = distance_km * 1000
        if tss_planned is not None:
            w["tssPlanned"] = tss_planned
        if sport is not None:
            sid = _sport_id(sport)
            w["workoutTypeFamilyId"] = sid
            w["workoutTypeValueId"] = sid
        if hidden is not None:
            w["isHidden"] = hidden
        if coach_comment is not None:
            w["coachComments"] = coach_comment
        resp = await self._request("PUT", f"/fitness/v6/athletes/{athlete_id}/workouts/{workout_id}", json=w)
        if resp.status_code not in (200, 204):
            raise ValueError(f"No se pudo actualizar la sesión: HTTP {resp.status_code} {resp.text[:200]}")
        return {"ok": True, "workout_id": workout_id, "titulo": w.get("title"),
                "fecha": (w.get("workoutDay") or "").split("T")[0], "oculta": bool(w.get("isHidden"))}

    async def delete_workout(self, athlete: str, workout_id: str) -> dict[str, Any]:
        _writes_enabled()
        athlete_id = await self._resolve_athlete(athlete)
        w = await self._get_workout_raw(athlete_id, workout_id)
        if w.get("completed") or (w.get("totalTime") or 0) > 0:
            raise ValueError("Esa sesión ya tiene datos del atleta (completada). No la borro por seguridad.")
        resp = await self._request("DELETE", f"/fitness/v6/athletes/{athlete_id}/workouts/{workout_id}")
        if resp.status_code not in (200, 204):
            raise ValueError(f"No se pudo borrar la sesión: HTTP {resp.status_code}")
        return {"ok": True, "borrada": workout_id, "titulo": w.get("title")}

    async def release_week(self, athletes: list[str] | None = None, group: str | None = None,
                           week_of: str | None = None) -> dict[str, Any]:
        _writes_enabled()
        monday, sunday = _week_bounds(week_of)
        targets: list[str | int] = list(athletes or [])
        if group:
            gs = [g for g in (await self.list_groups())["groups"] if group.lower() in g["name"].lower()]
            if not gs:
                raise ValueError(f"No encontré la carpeta '{group}'.")
            targets += [a["athlete_id"] for g in gs for a in g["athletes"]]
        if not targets:
            raise ValueError("Indica atletas o una carpeta.")
        out = []
        for t in targets:
            data = await self.get_workouts(t, monday.isoformat(), sunday.isoformat())
            freed = []
            for w in data["workouts"]:
                if w["hidden"] and w["status"] == "planificado" and w["id"]:
                    await self.update_workout(str(data["athlete_id"]), w["id"], hidden=False)
                    freed.append(f"{w['date']} {w['title']}")
            out.append({"athlete_id": data["athlete_id"], "liberadas": freed})
        return {"semana": {"inicio": monday.isoformat(), "fin": sunday.isoformat()}, "resultado": out}

    async def get_weekly_summary(self, athlete: str | int, week_of: str | None = None) -> dict[str, Any]:
        ref = datetime.strptime(week_of, "%Y-%m-%d").date() if week_of else date.today()
        monday = ref - timedelta(days=ref.weekday())
        sunday = monday + timedelta(days=6)

        workouts, fitness = await asyncio.gather(
            self.get_workouts(athlete, monday.isoformat(), sunday.isoformat()),
            self.get_fitness(athlete, start_date=monday.isoformat(), end_date=sunday.isoformat()),
        )

        total_tss = sum(w["tss"] for w in workouts["workouts"] if w.get("tss") is not None)
        total_hours = sum(
            (w.get("duration_hours_actual") or w.get("duration_hours_planned") or 0)
            for w in workouts["workouts"]
        )
        return {
            "week": {"start": monday.isoformat(), "end": sunday.isoformat()},
            "workout_count": workouts["count"],
            "total_tss": round(total_tss, 1),
            "total_duration_hours": round(total_hours, 2),
            "fitness": fitness["daily_data"][-1] if fitness["daily_data"] else None,
            "workouts": workouts["workouts"],
        }


# ---------------------------------------------------------------------------
# PASO 2 — El servidor MCP (modo web / HTTP)
# ---------------------------------------------------------------------------
# host="0.0.0.0" y el puerto de la variable PORT permiten que Railway (u otro
# hosting) exponga el servidor a internet. El endpoint queda en /mcp
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "trainingpeaks-demo",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
)
client = TPClient()


@mcp.tool()
async def tp_get_profile() -> dict[str, Any]:
    """Devuelve el perfil de la cuenta en TrainingPeaks (nombre, email, tipo de
    cuenta, si es coach y cuántos atletas tiene)."""
    return await client.get_profile()


@mcp.tool()
async def tp_list_athletes(query: str | None = None) -> dict[str, Any]:
    """Lista los atletas disponibles (en una cuenta de coach, los que entrena).

    Úsala para encontrar el nombre exacto o el athleteId de un atleta antes de
    pedir sus entrenamientos, forma o resumen semanal.

    Args:
        query: Texto opcional para filtrar por nombre (p. ej. "erick").
    """
    return await client.list_athletes(query=query)


@mcp.tool()
async def tp_get_workouts(athlete: str, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    """Lista los entrenamientos (planificados y completados) de un atleta entre dos fechas.

    Si no se indican fechas, devuelve los últimos 7 días.

    Args:
        athlete: Nombre o athleteId del atleta (usa tp_list_athletes para verlos).
        start_date: Fecha inicial en formato YYYY-MM-DD. Por defecto, hace 7 días.
        end_date: Fecha final en formato YYYY-MM-DD (máximo 90 días de rango). Por defecto, hoy.
    """
    return await client.get_workouts(athlete, start_date, end_date)


@mcp.tool()
async def tp_preview_copy_week(athlete: str, source_week: str, target_week: str,
                               sports: list[str] | None = None,
                               weekdays: list[int] | None = None) -> dict[str, Any]:
    """Previsualiza copiar entrenamientos planificados entre semanas, con detección
    de conflictos por fecha y deporte. weekdays: 0=lunes, 6=domingo.
    No copia actividades completadas ni realiza escrituras.
    """
    return await client.preview_copy_week(athlete, source_week, target_week, sports, weekdays)


@mcp.tool()
async def tp_preview_workout_edit(athlete: str, workout_id: str,
                                  tss_planned: float | None = None,
                                  coach_comment: str | None = None) -> dict[str, Any]:
    """Previsualiza una edición sin aplicarla. Rechaza editar TSS planificado
    en sesiones completadas porque eso no corrige el TSS real.
    Para ejecutar cambios permitidos se requiere confirmación del coach.
    """
    return await client.preview_workout_edit(athlete, workout_id, tss_planned, coach_comment)


@mcp.tool()
async def tp_audit_tss_range(athlete: str, start_date: str, end_date: str,
                             tss_per_hour_alert: float = 150.0) -> dict[str, Any]:
    """Audita TSS/h de sesiones completadas y natación con TSS casi nulo.
    No modifica actividades ni zonas; las alertas requieren validación del coach.
    """
    return await client.audit_tss_range(athlete, start_date, end_date, tss_per_hour_alert)


@mcp.tool()
async def tp_get_workout_details(athlete: str, workout_id: str) -> dict[str, Any]:
    """Lee detalles de una actividad individual sin modificarla.
    Devuelve campos disponibles de intensidad/TSS y señala explícitamente los ausentes.
    No confirma que las zonas actuales estén actualizadas; requiere otra fuente de umbrales.
    """
    return await client.get_workout_details(athlete, workout_id)


from tp_zone_adapter import get_athlete_training_zones


@mcp.tool()
async def tp_get_athlete_training_zones(athlete: str, sport: str, metric: str) -> dict[str, Any]:
    """Lee zonas individuales del atleta si se ha verificado y configurado
    un endpoint de TrainingPeaks. Solo lectura. Nunca inventa límites.
    sport: cycling, running, swimming. metric: hr, pace, power.
    """
    return await get_athlete_training_zones(client, athlete, sport, metric)


@mcp.tool()
async def tp_get_fitness(athlete: str, days: int = 90, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    """Devuelve la curva de forma de un atleta: CTL (fitness), ATL (fatiga) y TSB (frescura).

    Args:
        athlete: Nombre o athleteId del atleta (usa tp_list_athletes para verlos).
        days: Días de historial hacia atrás (por defecto 90). Se ignora si pasas fechas.
        start_date: Fecha inicial opcional (YYYY-MM-DD).
        end_date: Fecha final opcional (YYYY-MM-DD).
    """
    return await client.get_fitness(athlete, days=days, start_date=start_date, end_date=end_date)


@mcp.tool()
async def tp_get_weekly_summary(athlete: str, week_of: str | None = None) -> dict[str, Any]:
    """Resumen semanal de un atleta: nº de entrenos, TSS total, horas totales y forma al cierre.

    Args:
        athlete: Nombre o athleteId del atleta (usa tp_list_athletes para verlos).
        week_of: Cualquier fecha dentro de la semana (YYYY-MM-DD). Por defecto, la semana actual.
    """
    return await client.get_weekly_summary(athlete, week_of=week_of)


@mcp.tool()
async def tp_list_groups() -> dict[str, Any]:
    """Lista las carpetas (grupos) de atletas del coach tal como se ven en
    TrainingPeaks (p. ej. "A Lunes", "B Martes", "Packs"), con los atletas de cada una."""
    return await client.list_groups()


@mcp.tool()
async def tp_review_week(groups: list[str] | None = None, week_of: str | None = None) -> dict[str, Any]:
    """Revisa la semana de los atletas por carpeta y devuelve, para cada uno:
    SIN CARGA (no tiene sesiones), CARGA OCULTA (todo lo pendiente está oculto,
    no liberado), EN ROJO (sesiones no hechas o muy por fuera de lo planificado) u OK.

    Args:
        groups: Nombres (o parte) de las carpetas a revisar, p. ej. ["A Lunes", "Packs"].
                Si se omite, revisa todas las carpetas menos la de por defecto.
        week_of: Cualquier fecha de la semana (YYYY-MM-DD). Por defecto, la actual.
    """
    return await client.review_week(groups, week_of)


@mcp.tool()
async def tp_create_workout(athlete: str, date: str, sport: str, title: str,
                            duration_minutes: float | None = None, description: str | None = None,
                            distance_km: float | None = None, tss_planned: float | None = None,
                            hidden: bool = False) -> dict[str, Any]:
    """Crea una sesión planificada en el calendario de un atleta. EDITA TrainingPeaks:
    confirma con el coach antes de usarla.

    Args:
        athlete: Nombre o athleteId.
        date: Día (YYYY-MM-DD).
        sport: natacion, ciclismo, carrera, brick, fuerza, crosstrain, descanso u otro.
        title: Título de la sesión.
        duration_minutes, distance_km, tss_planned, description: opcionales.
        hidden: True para dejarla oculta al atleta (sin liberar).
    """
    return await client.create_workout(athlete, date, sport, title, duration_minutes,
                                       description, distance_km, tss_planned, hidden)


@mcp.tool()
async def tp_update_workout(athlete: str, workout_id: str, title: str | None = None,
                            description: str | None = None, date: str | None = None,
                            duration_minutes: float | None = None, distance_km: float | None = None,
                            tss_planned: float | None = None, sport: str | None = None,
                            hidden: bool | None = None, coach_comment: str | None = None) -> dict[str, Any]:
    """Edita una sesión existente: título, descripción, mover de día, duración,
    distancia, TSS, deporte, ocultar/liberar o comentario del coach. Solo cambia
    los campos que se pasen. EDITA TrainingPeaks: confirma con el coach antes.

    Args:
        athlete: Nombre o athleteId.
        workout_id: id de la sesión (sale en tp_get_workouts).
        date: nuevo día (YYYY-MM-DD) para moverla.
        hidden: False la libera (visible), True la oculta.
    """
    return await client.update_workout(athlete, workout_id, title, description, date,
                                       duration_minutes, distance_km, tss_planned, sport,
                                       hidden, coach_comment)


@mcp.tool()
async def tp_delete_workout(athlete: str, workout_id: str) -> dict[str, Any]:
    """Borra una sesión PLANIFICADA (nunca borra una que el atleta ya completó).
    Es irreversible: confirma con el coach antes de usarla."""
    return await client.delete_workout(athlete, workout_id)


@mcp.tool()
async def tp_release_week(athletes: list[str] | None = None, group: str | None = None,
                          week_of: str | None = None) -> dict[str, Any]:
    """Libera (hace visibles) todas las sesiones ocultas de la semana para los
    atletas indicados o para toda una carpeta. EDITA TrainingPeaks: confirma antes.

    Args:
        athletes: Lista de nombres o athleteIds.
        group: Nombre (o parte) de una carpeta, p. ej. "Packs".
        week_of: Cualquier fecha de la semana (YYYY-MM-DD). Por defecto, la actual.
    """
    return await client.release_week(athletes, group, week_of)


# ---------------------------------------------------------------------------
# Librerías y planes (solo lectura) — para migrar a Intervals.icu
# ---------------------------------------------------------------------------
from tp_libraries_tools import register_library_tools


async def _tp_get_url(url: str) -> Any:
    headers = await client._auth_headers()
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
        resp = await c.get(url, headers=headers)
    if resp.status_code != 200:
        raise ValueError(f"TrainingPeaks respondió HTTP {resp.status_code} en {url.split('?')[0]}")
    return resp.json() if resp.content else None


register_library_tools(mcp, _tp_get_url)

from wolfpack_migration import register_migration_tools
register_migration_tools(mcp, _tp_get_url)


class _NormalizeMcpPath:
    """Hace que /mcp (sin barra final) funcione igual que /mcp/ sin redirect 307.

    Claude.ai guarda la URL del conector sin barra final, y FastMCP monta el
    endpoint con Starlette Mount("/mcp"), que responde 307 -> /mcp/. Detras del
    proxy TLS de Railway ese redirect sale como http:// y el cliente lo rechaza.
    Reescribimos la ruta a /mcp/ antes de enrutar: nunca hay redirect.

    Si MCP_SECRET está configurada, SOLO responde en /mcp/<MCP_SECRET>;
    cualquier otra ruta recibe 404 (así nadie sin la clave lee ni edita).
    """

    def __init__(self, app):
        self.app = app
        self.secret = os.environ.get("MCP_SECRET", "").strip()

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if self.secret:
                base = f"/mcp/{self.secret}"
                if path in (base, base + "/"):
                    path = "/mcp/"
                else:
                    await send({"type": "http.response.start", "status": 404,
                                "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"Not found"})
                    return
            elif path == "/mcp":
                path = "/mcp/"
            if path != scope.get("path"):
                scope = dict(scope)
                scope["path"] = path
                scope["raw_path"] = path.encode()
        await self.app(scope, receive, send)


if __name__ == "__main__":
    import uvicorn

    logger.info("Arrancando TrainingPeaks MCP (REMOTO) — edición %s", "ACTIVA" if os.environ.get("MCP_SECRET") else "desactivada (solo lectura)")
    app = _NormalizeMcpPath(mcp.streamable_http_app())
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        forwarded_allow_ips="*",
        proxy_headers=True,
    )
