# Verificación de endpoints de zonas TrainingPeaks — 2026-11-19

## Hallazgos documentales
- API de partners (documentación pública): GET https://api.trainingpeaks.com/v1/coach/athletes/{athlete_id}/zones; scope coach:athletes. Respuesta HeartRateZones, SpeedZones, PowerZones; velocidad en m/s. Fuente: https://github-wiki-see.page/m/TrainingPeaks/PartnersAPI/wiki/Coach-Get-Athlete-Zones
- Candidato de API interna usado por implementación de terceros: GET https://tpapi.trainingpeaks.com/fitness/v1/athletes/{athleteId}/settings, con heartRateZones, powerZones y speedZones. Fuente: https://github.com/nagelflorian/trainingpeaks-mcp-server/blob/main/CLAUDE.md
- TrainingPeaks indica que la API oficial requiere aprobación de desarrollador: https://help.trainingpeaks.com/hc/en-us/articles/234441128-TrainingPeaks-API

## Verificación pendiente
No se ha realizado una solicitud autenticada con credenciales del coach al endpoint de zonas. No afirmar permisos, payload ni bpm de Nicolás (6664356) hasta recibir HTTP 200 y validar la respuesta.

## Próxima prueba segura
En un entorno aislado autorizado, ejecutar solo GET /fitness/v1/athletes/6664356/settings con el token del TPClient existente; registrar únicamente HTTP status y nombres de claves, nunca token, cookies ni datos sensibles. Si devuelve 200, mapear zonas por deporte y métrica y normalizar según el esquema real. Si devuelve 401/403, investigar permisos; si 404, revisar versión del endpoint. No hacer PUT ni cambiar entrenamientos. Mantener producción sin cambios.

## Nota técnica
El adaptador actual exige TP_ZONE_ENDPOINT_TEMPLATE con tres placeholders, pero el endpoint de settings solo usa athleteId. Será necesario ajustar el adaptador a esta ruta concreta y al esquema anidado antes de integrarlo y probarlo. No reutilizar el esquema normalizado como si fuera la respuesta original.
