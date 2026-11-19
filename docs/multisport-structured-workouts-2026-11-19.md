# WOLFPACK-SXH — Especificación multideporte de entrenamientos estructurados
Fecha: 2026-11-19. Rama: feature/tss-audit. Estado: especificación, no implementación ni despliegue.

## Alcance
El MCP debe leer zonas individuales por deporte y construir/editar sesiones estructuradas con objetivos por:
- Ciclismo: frecuencia cardíaca (bpm, zonas individuales) y potencia (W, %FTP) cuando corresponda.
- Running: frecuencia cardíaca (bpm), ritmo (min/km) y opcionalmente potencia (W).
- Natación: ritmo (min/100 m, o unidad configurada) y opcionalmente frecuencia cardíaca si TrainingPeaks la admite.
- Fuerza: bloques de ejercicios, repeticiones, series y descansos cuando la API permita estructuras.

## Contrato de lectura
tp_get_athlete_training_zones(athlete, sport, metric): recuperar límites de zonas, unidad, umbral, fecha de actualización, origen y estado de disponibilidad. No asumir que las zonas de FC para ciclismo y running coinciden. No convertir zonas de ritmo entre carrera y natación. Si no hay zonas válidas, detener la generación de objetivos numéricos.

## Contrato de edición
tp_preview_structured_workout(athlete, workout_id?, date?, sport, blocks): comprobar deporte, unidades, objetivos y duraciones/distancias, detectar colisiones y presentar diferencias.
tp_apply_structured_workout(preview_id, expected_revision): crear o modificar tras confirmación del coach, y volver a leer para verificar persistencia.

Bloques: warmup, work, recovery, cooldown, repeat; target metric hr|pace|power, zone_id o límites explícitos verificados; duración en segundos o distancia en metros; metadatos de cadencia opcionales. Mantener objetivos de zona como referencia vinculada cuando la API lo permita, de lo contrario resolver los límites exactos con su versión.

## Validación
- Zonas de ciclismo por FC y de running por FC son independientes.
- Ritmo running min/km y natación min/100m: conversiones de unidades explícitas y probadas.
- No crear estructuras si el endpoint de TrainingPeaks no las soporta: informar la limitación y ofrecer descripción provisional.
- Probar lectura, escritura y round-trip en entorno aislado; permisos y rollback.
- Prohibido alterar actividades completadas, umbrales o TSS históricos sin autorización específica.
- Producción Railway incredible-youth/web permanece en main hasta pasar pruebas.

## Casos de aceptación
1. Nicolás Gil (6664356), media maratón 2026-11-22 (4067555326): 21.1 km en Z2 FC running individual, sin inventar bpm.
2. Ciclismo: sesión Z2 por FC usando zonas de ciclismo, no zonas de carrera.
3. Running: intervalos por ritmo con zonas personales.
4. Natación: 6x200m por ritmo objetivo min/100m y recuperaciones definidas.
5. Cuando falten zonas o endpoints, mostrar error accionable sin modificar el calendario.
