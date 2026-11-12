# ADR-013 — Silencio al paciente en errores técnicos, notificación configurable al equipo

**Fecha:** 7-sep-2026 (aceptada) — 13-sep-2026 (implementada y validada)
**Estado:** ✅ Implementada y validada
**Decide:** Alejandro Casillas

## Contexto

Hasta esta sesión, cuando un tool compartido entre `ai_agent_admin` y `ai_agent_user`
fallaba por una causa técnica real (Calendar caído, error de Postgres, etc.), el mensaje
de error terminaba regresando a quien llamó el tool — incluyendo al paciente, si fue
`ai_agent_user` quien lo disparó. Esto genera fricción innecesaria en el cliente final
del cliente de Cupototal (el paciente), quien no necesita ni debe lidiar con detalles
técnicos de una falla interna.

## Decisión

1. **✅ Confirmado (13-sep-2026, Opción B):** el paciente recibe un mensaje **genérico**
   ante un error técnico ("Hubo un problema al agendar tu cita. El equipo del
   consultorio te contactará pronto.") — no el mensaje técnico real, pero tampoco
   silencio total (la idea original de "silencio absoluto" se descartó: el prompt de
   `ai_agent_user` ya tenía un mensaje de continuidad de negocio construido, y dejar al
   paciente sin ninguna respuesta genera peor experiencia que un aviso genérico).
2. **El equipo (admin/secretaria/lo que el cliente defina) sí es notificado**, vía un
   canal y contacto configurables por cliente en una tabla nueva
   (`configuracion_notificaciones`), no hardcodeado como hasta ahora.
3. **Los nodos de mensaje de error al paciente que ya existen NO se eliminan** — se
   desactivan por defecto (`notificar_error_paciente = false`) y quedan disponibles para
   activarse por cliente si algún caso de negocio futuro lo requiere.
4. **Alcance:** aplica a errores técnicos reales (fallo de infraestructura/integración).
   NO aplica a resultados de negocio normales que el paciente sí necesita ver (ej. "ese
   horario ya se ocupó", mensaje de escalación esperada cuando el bot no puede resolver
   algo directo, o el aviso de rate limit) — esos siguen visibles, son información útil,
   no errores.

## Auditoría retroactiva (7-sep-2026)

| Tool | ¿Paciente ve error técnico hoy? | Acción |
|---|---|---|
| `tool-cancelar-cita` | No — admin-exclusivo | Ninguna, ya cumple |
| `tool-reagendar-cita` | No — admin-exclusivo | Ninguna, ya cumple |
| `tool-bloquear-fecha` | No — admin-exclusivo | Ninguna, ya cumple |
| `tool-marcar-alerta-atendida` | No — admin-exclusivo | Ninguna, ya cumple |
| `tool-crear-alerta-fallback` | Mensaje de escalación esperada, no error técnico | Fuera de alcance de este ADR (zona gris, confirmar si aplica) |
| `tool-agendar-cita` | **Sí — rama `agendar_event_error`** (único tool llamado directo por `ai_agent_user`) | **✅ Corregido e implementado (13-sep-2026)** — ver sección "Implementación" abajo |
| Rate limit (workflow principal) | Mensaje de fricción intencional, no error técnico | Fuera de alcance de este ADR (zona gris, confirmar si aplica) |

## Implementación real (13-sep-2026)

Construida junto con el parámetro `origen` (roadmap ítem 1) — ver `ARQ-001` para el
detalle nodo por nodo. Resumen:

- Tabla `configuracion_notificaciones` creada, 1 fila para `cliente_id='cliente_piloto'`
  (`notificar_error_paciente=false`, `canal_equipo='whatsapp'`,
  `contacto_equipo='+52XXXXXXXXXX'`).
- **Alcance recortado, aprobado por el autor:** `canal_equipo`/`contacto_equipo` de la
  tabla NO se conectaron al mecanismo real de notificación al equipo — ese sigue
  siendo `Crear Alerta Fallo Técnico` (`tool-crear-alerta-fallback`), sin cambios,
  porque hoy solo existe 1 canal y 1 destinatario real — reconectar sería trabajo sin
  beneficio inmediato. La tabla se usa SOLO para decidir `notificar_error_paciente`.
  Se conecta `canal_equipo`/`contacto_equipo` cuando exista una razón de negocio real
  (más de un canal o cliente).
- Nodos nuevos en `tool-agendar-cita`: `Lookup Config Notificaciones` (Postgres) →
  `¿Silenciar error al paciente?` (IF, evalúa `origen='paciente_whatsapp'` AND
  `notificar_error_paciente=false`) → `agendar_event_error_generico` (nuevo, mensaje
  suave) o `agendar_event_error` (existente, mensaje técnico real).
- Insertado entre `Crear Alerta Fallo Técnico` (sin cambios, sigue notificando al
  equipo siempre) y los nodos terminales de error.

**Validado end-to-end (13-sep-2026), evidencia real (Execute Workflow manual, forzando
fallo real de Calendar con `end` anterior a `start`):**
- `origen='admin_whatsapp'` → mensaje técnico real (`agendar_event_error`), como exige
  la `REGLA NO NEGOCIABLE` del prompt del admin.
- `origen='paciente_whatsapp'` → mensaje genérico (`agendar_event_error_generico`),
  sin detalle técnico.
- Confirmado en ambos casos que `Crear Alerta Fallo Técnico` sigue notificando al
  equipo (mecanismo sin cambios).

## Diseño técnico

### Tabla nueva

```sql
CREATE TABLE configuracion_notificaciones (
    cliente_id              TEXT PRIMARY KEY,
    notificar_error_paciente BOOLEAN NOT NULL DEFAULT false,
    canal_equipo             TEXT NOT NULL DEFAULT 'whatsapp',
    contacto_equipo          TEXT NOT NULL,
    activo                   BOOLEAN NOT NULL DEFAULT true,
    actualizado_en           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Patrón por tool (donde aplique)

```
Nodo de error técnico existente
        │
        ▼
Lookup configuracion_notificaciones (por cliente_id)
        │
        ├── notificar_error_paciente = true  → mensaje de error existente al paciente (sin tocar)
        └── notificar_error_paciente = false → respuesta neutra/silenciosa al paciente
                                                  + notificación al equipo (canal_equipo/contacto_equipo)
```

Nodos existentes de mensaje de error al paciente se conservan intactos, nunca se
eliminan — el flag decide cuál rama corre.

## Pendientes — confirmados durante esta sesión (7 y 13-sep-2026)

1. ✅ **`tool-crear-alerta-fallback` (mensaje de escalación) queda fuera de esta regla**
   — es mensaje de negocio esperado, no error técnico.
2. ✅ **El aviso de rate limit al paciente queda fuera de esta regla** — mismo motivo.
3. ✅ **`canal_equipo` se deja como texto libre con default `'whatsapp'`** — no se
   construye soporte de email ni otros canales hasta que haya demanda real de un
   cliente (ver "Implementación real" arriba).

## Consecuencias

- Reduce fricción real para el paciente final de cada cliente de Cupototal.
- Introduce una tabla de configuración nueva — primer paso hacia soporte multi-cliente
  real de comportamiento personalizable (relevante también para Ola 4 — Multi-agente).
- Requiere tocar `tool-agendar-cita` como parte de su implementación ya planeada
  (roadmap punto A) — no es trabajo adicional aislado, se integra en esa sesión.
