"""Initialize a shared, non-sensitive WOLFPACK ecosystem registry in iCloud.
Safe by default: never overwrites existing files.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/WOLFPACK AI OS/WOLFPACK_BRAIN_STARTER"
registry = ROOT / "09_CONECTORES" / "NETLIFY"
registry.mkdir(parents=True, exist_ok=True)
now = datetime.now(timezone.utc).isoformat()
sites = [
 {"name":"MANADA SITE","url":"https://wolfpack-manada.netlify.app","source_status":"published HTML; original Claude project not yet recovered","git_repository":None,"production_change_allowed":False},
 {"name":"MANADA STAFF","url":"https://wolfpack-manada-staff.netlify.app","source_status":"published deployment; original project not yet recovered","git_repository":None,"production_change_allowed":False},
]
files = {
 registry / "INVENTARIO_SITIOS.json": json.dumps({"schema_version":1,"created_utc":now,"inventory_status":"partial; verify all Netlify sites","sites":sites},ensure_ascii=False,indent=2)+"\n",
 registry / "REGISTRO_DESPLIEGUES.md": "# Despliegues Netlify WOLFPACK\n\nInventario inicial parcial. Ningún cambio de producción autorizado.\n\nRegistrar cada despliegue con sitio, commit, origen, fecha, responsable, prueba y rollback.\n",
 ROOT / "ESTADO_ECOSISTEMA.md": """# Estado del ecosistema WOLFPACK AI OS

## Fuentes oficiales
- GitHub: código fuente versionado; no guardar secretos.
- Supabase: datos operativos, reservas, roles y seguimiento (sujeto a revisión RLS).
- iCloud Drive: documentación y expedientes; no base de datos transaccional.
- Netlify: despliegue web.
- Railway: servicios Wolfy y conectores.
- TrainingPeaks: integración pendiente de validar para lectura y permisos.

## Sitios prioritarios
- MANADA SITE: https://wolfpack-manada.netlify.app
- MANADA STAFF: https://wolfpack-manada-staff.netlify.app

## Estado verificado
- 124 expedientes deportivos locales estructurados y verificados por Terminal del Mac mini.
- Código fuente original de MANADA pendiente de recuperar.
- Conexión directa de Claude al Mac pendiente.
- Integración de WhatsApp y Wolfy pendiente de implementación.

## Política de colaboración
- Nunca sobrescribir archivos de Claude ni de otros agentes sin revisión.
- Guardar cambios en ramas y hacer pruebas antes de producción.
- Mantener IDs estables; no duplicar perfiles de atletas.
- No incluir datos médicos ni secretos en repositorios o respaldos sin controles específicos.
- No asumir que iCloud sincronizado equivale a acceso remoto autorizado para ChatGPT.
""",
}
for path, body in files.items():
    if path.exists():
        print("CONSERVADO:", path)
    else:
        with path.open("x",encoding="utf-8") as f:
            f.write(body)
        print("CREADO:", path)
print("REGISTRO INICIAL LISTO")
