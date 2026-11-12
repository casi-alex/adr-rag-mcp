# ADR-006 — Diseño de `tool-cancelar-cita`

**Estado:** Aceptado e implementado
**Fecha:** 16 agosto 2026
**Contexto del proyecto:** Cupototal — Ola 2, admin-vía-bot

## Contexto

Con `tool-buscar-cita` y `tool-reagendar-cita` ya completados y validados (ADR-005), el
siguiente sub-tool del Admin Agent es la cancelación de citas. Se necesitaba definir:
retención de datos al cancelar, reutilización de herramientas existentes, y manejo de
fallos parciales (la cancelación en Calendar puede tener éxito aunque pasos posteriores
fallen).

## Decisiones

### 1. Soft-delete en `citas_pacientes`

**Decisión:** al cancelar, la fila en `citas_pacientes` no se borra — se marca con
`estado = 'cancelada'` y `cancelado_at = NOW()` (columnas agregadas vía `ALTER TABLE`).

**Alternativa descartada:** `DELETE` de la fila.

**Razón:** coherente con el enfoque documentation-first del proyecto — mantener
trazabilidad de auditoría de qué citas existieron y cuándo se cancelaron, sin depender
de reconstruir ese historial desde Google Calendar (que si el evento se borra, pierde
el registro por completo).

### 2. Reutilización de `tool-buscar-cita`, sin duplicar lógica

**Decisión:** `tool-cancelar-cita` no reimplementa búsqueda — si no recibe `event_id`,
llama a `tool-buscar-cita` (mismo patrón que `tool-reagendar-cita`, ADR-005: separación
estricta de tools de lectura y escritura).

**Consecuencia de diseño no anticipada:** `tool-buscar-cita` regresa un solo campo
`output` de texto ya formateado para humanos (no un array estructurado). Esto obligó a
extraer `event_id` del texto vía regex en la rama de "1 resultado", en vez de leer un
campo estructurado. Ver PM-004 para el detalle del debugging que reveló esto.

### 3. Delete de Calendar vía HTTP Request, no nodo nativo

**Decisión:** igual que `tool-reagendar-cita` (que usa `PATCH` vía HTTP Request por el
gotcha ya documentado de "Bad request" en el nodo nativo Update), `tool-cancelar-cita`
usa `HTTP Request` con método `DELETE` en vez del nodo nativo Google Calendar → Delete.

**Razón:** consistencia preventiva con el gotcha ya conocido, aunque el nodo nativo
Delete nunca se probó de forma aislada — se optó por no arriesgar el mismo fallo.

### 4. Manejo de fallos parciales — `exito` no siempre corresponde a "todo salió bien"

**Decisión:** el campo `exito` que el sub-workflow regresa al Admin Agent representa
específicamamente si **la cancelación en Calendar** ocurrió — no si cada paso posterior
(notificación al paciente) también tuvo éxito.

| Escenario | `exito` | `output` |
|---|---|---|
| Delete Calendar OK + Postgres OK + Twilio OK | `true` | Confirmación normal |
| Delete Calendar OK + Postgres OK + Twilio falla (error real) | `true` | Cancelada, pero avisa que falló la notificación |
| Delete Calendar OK + Postgres OK + sin teléfono registrado (cita creada manual en Calendar) | `true` | Cancelada, pero avisa que no hay teléfono para notificar |
| Delete Calendar falla (error real: credencial, rate limit, etc.) | `false` | La cancelación NO ocurrió, se reporta como error real |

**Razón:** el admin necesita saber inequívocamente si la cita sigue o no activa
en el calendario real (lo que importa operativamente) — un fallo de notificación no debe
reportarse como si la cita siguiera activa, pero tampoco debe ocultar que sí se canceló.

### 5. Caso descartado tras evidencia empírica: "evento no encontrado" al borrar

**Decisión:** no se construyó una rama específica para manejar "el evento ya no existe
en Calendar" como caso de error distinto.

**Razón:** se confirmó empíricamente que la API de Google Calendar responde `204`
(éxito) al hacer `DELETE` sobre un evento ya inexistente — no `404`. El escenario que se
había anticipado (condición de carrera: cancelar dos veces, o confirmar sobre datos
obsoletos) simplemente no genera un error distinguible en este endpoint. La rama de
error del nodo Delete cubre únicamente fallos reales de la API (credenciales, rate
limit, disponibilidad del servicio).

## Consecuencias

- `tool-cancelar-cita` quedó con 3 salidas finales posibles (éxito completo, éxito con
  aviso de notificación fallida por error, éxito con aviso de notificación fallida por
  falta de dato) más 1 salida de error real — más ramas de las que originalmente se
  habían planeado (2), reflejando la complejidad real del sistema (múltiples puntos de
  fallo independientes en una sola acción de negocio).
- Reveló y forzó la corrección de 2 bugs preexistentes fuera de su propio alcance:
  `tool-agendar-cita` guardaba el `summary` del evento en vez del nombre real del
  paciente en `citas_pacientes.nombre_paciente`, y el filtro de nombre en
  `tool-buscar-cita` no revisaba el campo `description`. Ver PM-004.
