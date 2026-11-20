# Auditoría TSS de actividades completadas — propuesta técnica

Estado: diseño inicial; sin modificaciones en datos de atletas ni despliegue.

## Caso de validación: Nicolás Gil Bazzani (athlete_id 6664356)
- 2026-11-01, workout 3983622946: 2.0078 h, 373.03 TSS
- 2026-11-08, workout 4016069987: 1.1960 h, 310.38 TSS
- 2026-11-12, workout 3983622957: 0.6656 h, 125.57 TSS
- 2026-11-15, workout 3983622962: 1.0462 h, 235.53 TSS

Estos valores son banderas para inspección, no prueba de error ni autorización para reemplazarlos.

## Implementación propuesta
1. `tp_audit_completed_workout(athlete, workout_id)`: solo lectura. Recuperar actividad detallada, modalidad de TSS, potencia normalizada, ritmo ajustado/ritmo, FC, umbrales vigentes, deporte, duración, fuente del dispositivo y marcas de cálculo; distinguir campos no disponibles.
2. `tp_audit_tss_range(athlete, start_date, end_date)`: alertas de TSS/h inusual, discrepancias entre métodos y natación con TSS casi nulo. Evitar umbrales universales como diagnóstico.
3. `tp_preview_tss_correction(athlete, workout_id, method)`: cálculo hipotético solo cuando los datos y umbrales sean suficientes; preferir rTSS para running con umbral de ritmo validado, hrTSS como alternativa y potencia solo cuando esté calibrada.
4. `tp_apply_tss_correction(...)`: **NO implementar escritura** hasta confirmar una operación admitida por TrainingPeaks y su semántica. Exigir confirmación explícita por actividad, respaldo de valores originales, idempotencia, registro de auditoría y verificación posterior.
5. Reconsultar CTL/ATL/TSB después de correcciones efectivas. No escribir manualmente la curva.

## Restricciones
- El método actual `tp_update_workout` no ofrece cambiar el método de TSS de una actividad completada.
- El servidor usa endpoints internos de TrainingPeaks: comprobar soporte, estabilidad y permisos antes de añadir llamadas.
- No guardar contraseñas, cookies, tokens ni datos privados de atletas en repositorios o logs.
- No recalcular ni sobrescribir valores a partir de aproximaciones.
- Validar tests con respuestas simuladas antes de cualquier despliegue en Railway.
