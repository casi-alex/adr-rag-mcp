# ADR-014 — Normalización de teléfono por canal (formato canónico + conversión en envío)

**Fecha:** 7-sep-2026
**Actualizado:** 13-sep-2026 — implementada, validada end-to-end y migración de datos completada
**Estado:** ✅ Aceptada e implementada
**Decide:** Alejandro Casillas

## Contexto

El admin escribe el teléfono del paciente a mano al usar `tool-agendar-cita` (único
punto donde esto ocurre — cuando el paciente escribe él mismo, el teléfono ya llega en
formato real de WhatsApp desde el webhook). Confiar en que el admin siempre escriba el
prefijo correcto (`+521` con el "1" extra para WhatsApp en México, vs. `+52` sin "1"
para SMS/Voz — ver gotcha documentado, sesión 1-sep) es frágil: un error de formato
puede impedir que el mensaje le llegue al paciente.

## Decisión

La decisión de formato **nunca recae en el admin ni en el prompt del agente** — el
sistema normaliza automáticamente:

1. **Formato canónico guardado en `citas_pacientes`:** E.164 estándar, sin el "1" extra
   de WhatsApp — `+52XXXXXXXXXX`.
2. **Conversión al canal, solo en el momento de enviar:** cada canal (WhatsApp hoy;
   SMS/Voz en el futuro) tiene su propio nodo/expresión de conversión justo antes del
   nodo de envío — el dato guardado nunca cambia.
3. **Al agregar un canal nuevo** (SMS/Voz, roadmap Ola futura): no se toca el dato
   guardado ni los tools existentes — solo se agrega el nodo de conversión de ese canal
   nuevo en su propio punto de envío.

## Diseño técnico (as-built, 13-sep-2026)

```
Tool Trigger (tool-agendar-cita)
        │
        ▼
Normalizar Teléfono (nodo nuevo, Code)
  - Limpia todo lo que no sea dígito
  - Toma los últimos 10 dígitos reales (right(digits, 10))
  - Guarda SIEMPRE: +52XXXXXXXXXX (formato canónico)
        │
        ▼
Execute a SQL query (INSERT citas_pacientes)
  - Referencia explícita: $('Normalizar Teléfono').item.json.telefono_paciente
    (antes: $('Tool Trigger').item.json.telefono_paciente)
```

```
get_phone / cancel_user_date (SELECT/RETURNING telefono_paciente, canónico)
        │
        ▼
Formato WhatsApp (nodo nuevo, Code — uno por tool)
  - Toma los últimos 10 dígitos reales
  - Reconstruye: whatsapp:+521XXXXXXXXXX (formato exacto que espera Twilio)
        │
        ▼
send_wa_message (tool-reagendar-cita) / notify_user (tool-cancelar-cita)
```

**Desviación respecto al diseño original del ADR:** el pseudocódigo original
proponía lógica condicional por longitud de string (10 dígitos → antepone; 12
con "52" → normaliza). Se implementó en su lugar "tomar siempre los últimos 10
dígitos reales" — mismo patrón ya validado y probado robusto en la pieza 2 de
`tool-buscar-cita` (búsqueda por teléfono, 12-sep) — cubre también el caso de 13
dígitos (`521` + 10) que el pseudocódigo original no contemplaba explícitamente.

**Puntos de envío actualizados (3 de 3, confirmado contra ARQ-001):**
- `tool-agendar-cita`: `Normalizar Teléfono` antes del INSERT a `citas_pacientes`.
- `tool-reagendar-cita`: `Formato WhatsApp` entre `get_phone` y `send_wa_message`.
- `tool-cancelar-cita`: `Formato WhatsApp` entre `cancel_user_date` y `notify_user`.

**Confirmado fuera de alcance, sin cambios:** el nodo de envío del workflow
principal (`Send an SMS/MMS/WhatsApp message`) usa `to = twilio-whatsapp.body.From`
— el remitente crudo del webhook entrante, no un dato leído de `citas_pacientes` —
ya viene correctamente formateado por Twilio/Meta, no requiere normalización.

**Fuera de alcance (sin cambios, confirmado):** el teléfono del paciente cuando
escribe él mismo vía `ai_agent_user` llega ya en formato real de WhatsApp desde el
webhook, no pasa por `Normalizar Teléfono` para el envío de confirmación del
workflow principal (solo pasa por él al momento de guardarse en `citas_pacientes`
vía `tool-agendar-cita`, sin importar el origen).

## Migración de datos (13-sep-2026)

**Diagnóstico previo (dry-run, sin modificar nada):**

```sql
SELECT
  CASE
    WHEN telefono_paciente ~ '^\+521\d{10}$' THEN 'formato_521_con_1_extra'
    WHEN telefono_paciente ~ '^\+52\d{10}$' THEN 'formato_52_canonico'
    WHEN telefono_paciente ~ '^whatsapp:\+521\d{10}$' THEN 'formato_whatsapp_prefijo_521'
    WHEN telefono_paciente ~ '^whatsapp:\+52\d{10}$' THEN 'formato_whatsapp_prefijo_52'
    ELSE 'formato_desconocido'
  END AS patron,
  COUNT(*) AS cantidad
FROM citas_pacientes
GROUP BY patron
ORDER BY cantidad DESC;
```

**Resultado real (30 filas totales, 0 en canónico puro):** 17 filas en
`whatsapp:+52XXXXXXXXXX`, 13 en `whatsapp:+521XXXXXXXXXX` — el dato guardado
traía el prefijo `whatsapp:` completo, no solo la ambigüedad del "1" extra que el
ADR original tenía en mente. Esto obligó a desplegar primero los 3 puntos de
conversión (para no romper el envío real) y migrar la tabla al final, no antes.

**UPDATE aplicado (13-sep-2026):**

```sql
UPDATE citas_pacientes
SET telefono_paciente = '+52' || right(regexp_replace(telefono_paciente, '\D', '', 'g'), 10)
WHERE telefono_paciente NOT LIKE '+52__________'
   OR telefono_paciente LIKE '%whatsapp%';
```

**Resultado:** 30 filas actualizadas. Verificación posterior: 0 filas fuera del
formato canónico `+52XXXXXXXXXX`.

## Validación end-to-end (13-sep-2026), evidencia real

| Tool | Caso probado | Evidencia |
|---|---|---|
| `tool-agendar-cita` | Agendado nuevo (admin) | `telefono_paciente` guardado: `+52XXXXXXXXXX` (canónico puro, confirmado vía SQL) |
| `tool-agendar-cita` | Agendado nuevo (paciente, webhook real) | `telefono_paciente` guardado: `+52XXXXXXXXXX` (canónico puro) |
| `tool-reagendar-cita` | Reagendado sobre cita con teléfono formato viejo (`whatsapp:+52XXXXXXXXXX`) | `Formato WhatsApp` reconstruyó `whatsapp:+52XXXXXXXXXX`; Twilio aceptó (`status: queued`, `error_code: null`); **WhatsApp real recibido en `...XXXX`** |
| `tool-cancelar-cita` | Cancelación sobre cita con teléfono formato viejo (`whatsapp:+52XXXXXXXXXX`) | Mismo patrón; **WhatsApp real recibido en `...XXXX`** |

**Nota de método:** las pruebas de `tool-reagendar-cita`/`tool-cancelar-cita` se
ejecutaron vía Pin Data en `Tool Trigger` + Execute step (no había opción de
"Execute Workflow" manual disponible en esta instancia), apuntando a `event_id`
reales de pacientes de prueba ya existentes en la tabla con formato viejo. Primer
intento de confirmación de entrega real falló por sesión de Twilio Sandbox
expirada (>72h sin uso, no relacionado al código) — resuelto reuniendo el número
de prueba al Sandbox (`join <código>`) antes de reintentar.

## Consecuencias

- Elimina una clase entera de error humano (admin escribiendo mal el prefijo).
- Deja el sistema listo para agregar SMS/Voz sin retrabajo del dato guardado.
- Empaquetado con el punto A del roadmap porque toca directamente `tool-agendar-cita`,
  mismo tool que se va a modificar para el parámetro `origen` y para implementar
  ADR-013 — misma sesión de trabajo.
- **Hallazgo colateral durante la validación (no relacionado a este ADR):** se
  descubrió y corrigió un bug de wiring preexistente en `tool-agendar-cita`
  (ramas `false` de `¿Origen es admin?` sin nodo terminal conectado, PM-023) —
  documentado por separado en PM-024.
