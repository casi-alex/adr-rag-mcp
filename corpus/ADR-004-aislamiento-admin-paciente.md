# ADR-004: Aislamiento entre agente admin y agente paciente

**Fecha:** 15-ago-2026
**Estado:** Aceptado, implementado y validado end-to-end

## Contexto

Cupototal necesita soportar 2 tipos de interacción por el mismo número de
WhatsApp del bot:
- **Pacientes**: agendan citas nuevas.
- **Admin** (profesional de salud, vía whitelist): reagenda, cancela y bloquea fechas —
  acciones que modifican o eliminan citas de terceros (pacientes que no
  están en la conversación).

Había que decidir cómo separar (o no) el comportamiento y las herramientas
de ambos roles dentro del mismo AI Agent en n8n.

## Decisión

Se implementaron **2 AI Agents completamente separados** (no uno solo con
lógica condicional), cada uno con:
- Su propio toolset (paciente: agendar + consultar disponibilidad; admin:
  buscar, reagendar — cancelar y bloquear pendientes).
- Su propio System Message.
- **Memoria separada**: `session_id` con prefijo `admin:` para el agente
  admin, distinto del `session_id` del paciente, aunque ambos usen la misma
  tabla `n8n_chat_histories`.

Un nodo Postgres consulta la tabla `admin_whitelist` por número de teléfono
justo después del webhook; un IF bifurca hacia el agente correspondiente.

## Alternativas consideradas

| Opción | Por qué se descartó |
|---|---|
| Un solo agente, prompt dinámico según rol | El listado de tools del nodo AI Agent en n8n no es fácilmente condicional por ejecución — todas las tools conectadas quedan "disponibles" siempre, dependiendo de que el modelo obedezca instrucciones de texto para no usarlas |
| Un solo agente con todas las tools, prompt indica cuáles usar según rol | Descartado por seguridad: acciones como cancelar/reagendar son destructivas hacia terceros. Confiar el aislamiento a una instrucción de prompt (no a la arquitectura) es el mismo tipo de falla observada en otros bugs de esta sesión (ver Postmortem PM-003) — el LLM no es determinístico |

## Consecuencias

**Positivas:**
- Aislamiento real: el agente paciente nunca tiene acceso a tools
  destructivas, sin importar qué diga el prompt.
- Prompts más cortos y enfocados por rol.
- Un bug en lógica admin no puede afectar el flujo paciente.

**Negativas / costos aceptados:**
- Mantenimiento duplicado: datos compartidos (info del centro, tono) deben
  sincronizarse en 2 System Messages si cambian.
- Memoria separada implica que si el número del admin algún día escribe
  como paciente normal, no hay continuidad de contexto con su historial
  admin — se considera aceptable, no es un caso de uso actual.

## Confirmado en producción de pruebas

Validado con mensajes reales de WhatsApp: número admin correctamente
identificado y enrutado, número no-admin cae a flujo paciente sin
interrupciones.
