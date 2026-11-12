# ADR-005: Separar tools de búsqueda (lectura) y ejecución (escritura) para acciones admin

**Fecha:** 15-ago-2026
**Estado:** Aceptado, implementado y validado end-to-end (reagendar)

## Contexto

El primer diseño de `tool-reagendar-cita` combinaba en una sola llamada:
buscar la cita por nombre + fecha aproximada, y ejecutar el cambio en
Calendar. Esto obligaba al admin a dar la fecha actual de la cita antes de
que el agente pudiera hacer nada — flujo poco natural, y además el agente
de tool-calling podía encadenar "buscar" y "ejecutar" en el mismo turno sin
pausa real para confirmación humana, incluso cuando el prompt pedía
"confirma antes de ejecutar".

## Decisión

Se separó en 2 tools independientes:

- **`tool-buscar-cita`** (solo lectura): busca por nombre en rango amplio
  (próximos 30 días), regresa datos completos (paciente, fecha/hora,
  servicio, `event_id`, duración). No modifica nada.
- **`tool-reagendar-cita`** (escritura): recibe `event_id` + nueva fecha/hora
  ya resueltos, ejecuta el cambio en Calendar, notifica al paciente.

El admin ya no necesita saber ni dar la fecha actual de la cita — solo el
nombre. El agente muestra los datos encontrados y pide confirmación antes
de llamar la tool de escritura.

## Principio de diseño relacionado: no delegar cálculos determinísticos al LLM

Durante la implementación se identificó que el LLM calculaba mal, de forma
intermitente, datos que deberían ser determinísticos:
- Offset de zona horaria (`-05:00` en vez de `-06:00` fijo).
- Correspondencia día-de-semana → fecha calendario ("el martes" → fecha
  incorrecta).
- Duración de la cita al reagendar (asumía 1h en vez de preservar la
  duración original de 15 min).

**Decisión derivada:** cuando un dato es calculable con certeza a partir de
información ya disponible (duración = hora fin − hora inicio original), el
cálculo se hace en un nodo Code (Luxon) dentro del sub-workflow, no se le
pide al LLM que lo derive. El LLM solo maneja datos que genuinamente
requieren su criterio (qué cita busca el admin, si el paciente da el nombre
correcto, etc.).

## Consecuencias

**Positivas:**
- Conversación más natural (admin solo da el nombre, no fechas exactas).
- Reutilizable: `tool-buscar-cita` sirve también para `tool-cancelar-cita`
  sin reconstruir la lógica de búsqueda.
- Menor superficie de error: los cálculos determinísticos (duración,
  offset) ya no dependen de que el LLM "adivine bien".

**Negativas / costos aceptados:**
- Mayor cantidad de sub-workflows a mantener (2 en vez de 1).
- La confirmación en 2 turnos sigue sin estar garantizada al 100% por
  arquitectura — sigue dependiendo del prompt (ver nota en Postmortem
  PM-003). Se evaluará una solución de estado pendiente en Postgres en una
  ola futura si esto sigue fallando.
