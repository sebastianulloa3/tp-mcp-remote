"""
TrainingPeaks MCP — versión REMOTA de solo lectura
==================================================

Un servidor MCP mínimo que conecta TrainingPeaks con Claude (y cualquier
asistente compatible con el Model Context Protocol).

Esta versión es DELIBERADAMENTE limitada: SOLO LEE datos.
No crea entrenamientos, no escribe comentarios, no borra nada.

Expone estas herramientas:
  - tp_get_profile          -> tu perfil de atleta
  - tp_list_athletes        -> atletas disponibles (en cuenta de coach)
  - tp_get_workouts         -> entrenamientos en un rango de fechas
  - tp_get_fitness          -> curva de forma/fatiga (CTL / ATL / TSB)
  - tp_get_weekly_summary   -> resumen de una semana

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
                    "status": "completado" if completed else "planificado",
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


if __name__ == "__main__":
    logger.info("Arrancando TrainingPeaks MCP (REMOTO, solo lectura) en modo HTTP...")
    # transport="streamable-http": expone el servidor por HTTP en /mcp
    mcp.run(transport="streamable-http")
