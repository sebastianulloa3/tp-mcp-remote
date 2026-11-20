# TrainingPeaks MCP — zonas de FC y sesiones estructuradas

Estado: especificación técnica; NO desplegada ni implementada. 2026-11-19.

## Caso de aceptación
Atleta Nicolas Gil Bazzani (ID 6664356), test 2026-10-24 (workout 4017329519), carrera 2026-11-22 (workout 4067555326). Leer las zonas de carrera vigentes y su fecha de actualización, comparar con el test si el API expone métricas, y construir la sesión de 21,1 km dirigida por FC en Z2 real. No deducir FC a partir del tiempo del test.

## Herramientas propuestas
- tp_get_athlete_hr_zones(athlete, sport='run'): devolver umbral FC, método, límites inferior/superior de Z1/Z2, unidades bpm, fecha de modificación, fuente y flags de datos ausentes. Solo lectura.
- tp_get_workout_details(athlete, workout_id): exponer métricas realmente presentes del test, sin fabricar campos.
- tp_preview_structured_workout(athlete, workout_id, steps): validar estructura, deporte, zonas, objetivo de FC y mostrar diff. Solo lectura.
- tp_apply_structured_workout(athlete, workout_id, expected_revision, steps): escribir únicamente con confirmación del coach y soporte comprobado del endpoint; leer después para verificar estructura persistida.

## Seguridad y pruebas
1. Inspeccionar la respuesta real de los endpoints de TrainingPeaks en entorno aislado, sin registrar cookies ni credenciales.
2. Confirmar que el API permite lectura de zonas individuales y escritura de estructura (no asumirlo por la UI).
3. Tests con fixtures de zonas presentes/ausentes, distintas disciplinas, unidades y conflictos de revisión.
4. No sustituir zonas ni TSS, no editar actividades completadas y no publicar cambios a producción hasta que lectura, escritura y rollback estén probados.
5. Mantener el servicio production de Railway incredible-youth/web en main hasta validación.

## Plan de carrera provisional
2026-11-21: 30 min suave Z1/Z2. 2026-11-22: media maratón 21,1 km en Z2 por FC en descripción, todavía SIN estructura de intervalos con bpm verificados. Se eliminaron ciclismo del sábado y SwimFest del domingo.
