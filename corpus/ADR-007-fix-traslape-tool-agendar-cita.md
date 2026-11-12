# ADR-007 — Fix de traslape real en tool-agendar-cita

**Fecha:** 25 ago 2026
**Estado:** Aceptado, implementado y validado

## Contexto

`tool-agendar-cita` solo validaba disponibilidad vía `slot_locks` (match exacto de
`start_time`). Esto prevenía race conditions (dos requests simultáneos al mismo horario
exacto) pero no detectaba traslapes reales entre citas de horarios distintos con duración
suficiente para pisarse (ej. 10:00-10:15 y 10:05-10:20).

## Decisión

Agregar una validación de traslape real contra eventos existentes en Calendar, **antes**
del mecanismo de `slot_locks`, reutilizando la misma lógica de detección de traslape (
función `seTraslapan`) ya construida y validada en `tool-bloquear-fecha` el mismo día.

## Arquitectura

```
Trigger (start, end, summary, description, calendar_id, telefono_paciente, nombre_paciente)
  → Get Existing Events (Get Many, día completo del start)
       Success → Check Overlap (Code — compara [start,end) contra cada evento existente)
                    → ¿Hay traslape?
                         True  → horario_traslapado_error (terminal)
                         False → [flujo existente sin cambios]
                                 Postgres lock (slot_locks) → IF → Calendar Create → ...
       Error   → error_check_disponibilidad (terminal)
```

**Por qué antes del lock, no después:** evita insertar/borrar filas en `slot_locks` para
citas que de todos modos se van a rechazar por traslape real — más limpio, sin ensuciar la
tabla de locks con intentos fallidos.

## Validación

| Caso | Resultado |
|---|---|
| Sin traslape (caso normal) | Flujo sin cambios de comportamiento respecto a antes del fix |
| Traslape real, `start_time` distinto (10:00 vs 10:05, 15 min de duración) | Rechazado correctamente; nunca llega a `slot_locks` ni a Calendar Create; `0 rows` en `slot_locks` para ese horario |

## Pendiente (no bloqueante)

Caso borde de traslape exacto en el límite (nueva cita inicia exactamente cuando la
existente termina) no probado explícitamente — riesgo bajo, misma función ya validada en
`tool-bloquear-fecha` con múltiples casos.
