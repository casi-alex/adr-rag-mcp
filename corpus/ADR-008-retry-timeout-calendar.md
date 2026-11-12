# ADR-008 — Retry/timeout en mutaciones de Calendar

**Fecha:** 25 ago 2026
**Estado:** Aceptado, implementado

## Contexto

Los 4 tools que ejecutan mutaciones reales contra Google Calendar (`tool-agendar-cita`,
`tool-cancelar-cita`, `tool-reagendar-cita`, `tool-bloquear-fecha`) no tenían manejo de
fallos transitorios (rate limit puntual, hiccup de red) — cualquier fallo, por breve que
fuera, caía directo a la rama de error sin intentar de nuevo.

## Decisiones

### D1 — Retry + timeout, no solo uno de los dos
Se descartó implementar solo retry (deja al usuario esperando indefinidamente si Calendar
está genuinamente caído) o solo timeout/aviso (reporta como error fallos transitorios que
un reintento simple hubiera resuelto). Se implementan ambos: retry primero, timeout/aviso
como resultado final si los reintentos se agotan.

### D2 — Alcance: solo mutaciones reales, no búsquedas de solo lectura
Prioriza donde el riesgo real es mayor — una mutación fallida a medias puede dejar estado
inconsistente (lock huérfano, cita fantasma, bloqueo sin reagendar). Una búsqueda que falla
sin retry solo produce "no encontré resultados", impacto mucho menor. Mismo criterio de
priorización usado en todo el proyecto (filtro de "0 clientes pagando").

### D3 — Retry nativo de n8n, sin código custom
`Retry On Fail` (Settings de cada nodo): 3 intentos, 2000ms de espera entre cada uno. Se
descartó implementar retry manual vía Code node — el mecanismo nativo de n8n es suficiente
y no agrega complejidad de mantenimiento.

### D4 — Timeout explícito solo en el nodo HTTP, no en los nativos de Calendar
El nodo HTTP Request (PATCH de `tool-reagendar-cita`) tiene campo de Timeout explícito
(10000ms) en Options. Los nodos nativos de Google Calendar (Create/Delete, usados en los
otros 3 tools) no exponen ese campo en la UI de n8n. Se decidió **no migrarlos a HTTP
Request** solo por ganar control de timeout — el riesgo de reabrir gotchas ya resueltos
(ej. el nodo nativo Update que fallaba con "Bad request", ya reemplazado en su momento)
supera el beneficio, y Retry On Fail ya cubre el caso más común de fallo real (error con
respuesta, no cuelgue indefinido sin respuesta).

## Nodos afectados

| Nodo | Tool | Retry | Timeout explícito |
|---|---|---|---|
| Calendar Create | `tool-agendar-cita` | ✅ 3/2000ms | No aplica (nativo) |
| Delete Calendar Event | `tool-cancelar-cita` | ✅ 3/2000ms | No aplica (nativo) |
| HTTP PATCH a Calendar | `tool-reagendar-cita` | ✅ 3/2000ms | ✅ 10000ms |
| Create Block Event | `tool-bloquear-fecha` | ✅ 3/2000ms | No aplica (nativo) |

## Gap adicional encontrado y cerrado en el mismo fix

`Create Block Event` (tool-bloquear-fecha) no tenía manejo de error explícito — a diferencia
de los otros 3 nodos de mutación. Se agregó `Continue Using Error Output` + nodo Set
terminal (`error_create_block_event`), saltando `Insert Bloqueo` y todo el resto de la
cadena de reagendado si el bloqueo mismo nunca se creó.

## Mensajes de error actualizados

Los mensajes estáticos de error en `tool-agendar-cita` y `tool-reagendar-cita` se
actualizaron para mencionar "tras varios intentos", reflejando que el retry ya se agotó
antes de reportar el fallo. El mensaje dinámico de `tool-cancelar-cita` (que ya incluye el
detalle técnico del error real) se dejó sin cambio.
