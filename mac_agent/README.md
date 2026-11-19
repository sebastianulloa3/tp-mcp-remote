# WOLFPACK AI OS — puente de expedientes (fase 1)

**Estado:** prototipo local; NO está desplegado ni conectado automáticamente a iCloud.

## Seguridad y alcance
- TrainingPeaks sigue siendo fuente de datos; Railway coordinará la extracción en una fase posterior.
- No subir a GitHub los JSON reales con nombres/correos ni historiales médicos.
- El Mac mini es el único escritor de iCloud; no abrir puertos domésticos.
- Los expedientes se identifican por TP-ID y permanecen en `ATLETAS/EXPEDIENTES/TP-<id>`; una reclasificación no mueve documentos personales.
- `plan_active: null` indica **pendiente de verificación**, no ausencia de plan.
- Los eventos de competición se deben extraer por tramos de <=90 días para un máximo de 3 años y diferenciar resultados completados de carreras planificadas. Aún NO implementado.

## Uso cuando se tenga acceso al Mac
1. Verificar la ubicación real del HUB en iCloud Drive y crear la carpeta raíz.
2. Crear un manifiesto JSON privado con estructura `{"athletes":[{"athlete_id":6302438,"name":"Juan Barbosa","email":null,"group":"E. VIERNES (3)","plan_active":null}]}`.
3. Vista previa: `python3 mac_agent/icloud_sync.py --manifest /ruta/manifest.json --hub "/ruta/iCloud/WOLFPACK AI OS"`
4. Aplicar tras revisar: agregar `--apply`.
5. No instalar `launchd` ni programar sincronización hasta tener transporte autenticado, almacenamiento privado y pruebas de recuperación.

## Siguientes tareas
- Endpoint Railway autenticado para entrega incremental de manifiestos, sin secretos en URL.
- Clasificación fiable de plan asignado versus carga de calendario.
- Extracción de carreras de 3 años, deduplicación por ID y fecha, validación de resultados.
- Pruebas automáticas de idempotencia, symlinks y conflictos de archivos.
- Servicio `launchd` en macOS tras pruebas.
