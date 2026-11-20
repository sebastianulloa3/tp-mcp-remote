# Implementación priorizada — MCP TrainingPeaks WOLFPACK-SXH
2026-11-19 | Estado: plan de ejecución, NO desplegado.

## Fase 1 (bloqueante): lectura de zonas reales
1. Identificar endpoints permitidos de TrainingPeaks para zonas/umbrales por atleta, deporte y métrica. No asumir soporte de API privada.
2. Implementar tp_get_athlete_training_zones(athlete, sport, metric) en modo lectura con validación de límites y unidades: bpm ciclismo y carrera; min/km carrera; min/100m natación; potencia ciclismo si está disponible.
3. Tests para datos faltantes, umbrales obsoletos, límites ambiguos, autorización y aislamiento de atletas.
4. Caso de aceptación Nicolás Gil (6664356): obtener límites reales Z2 running y fecha de actualización; compararlos con test 24 octubre solo si API ofrece detalles suficientes. No deducir FC del tiempo de 3 km.

## Fase 2: escritura estructurada
1. Investigar y validar endpoints de bloques e intervalos sin editar producción.
2. Implementar previsualización con diferencias, objetivos de zonas, bloques, unidades, duración/distancia y detección de colisiones.
3. Implementar aplicación con confirmación explícita, control de revisión y verificación posterior.
4. Casos: media maratón Z2 FC, ciclismo por FC, running por ritmo, natación por ritmo, potencia cuando aplique.
5. Si el API no soporta lectura/escritura, responder con limitación concreta y no simular éxito.

## Fase 3: pruebas y despliegue controlado
Tests unitarios y de integración en staging; verificación de credenciales protegidas y compatibilidad con herramientas actuales; desplegar a Railway incredible-youth/web solo tras aprobación y resultados satisfactorios. Rollback a commit previo si falla.

**No modificar zonas, TSS históricos ni actividades completadas durante estas fases sin autorización adicional.**
