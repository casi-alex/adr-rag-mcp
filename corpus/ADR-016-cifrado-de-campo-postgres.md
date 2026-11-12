# ADR-016 — Cifrado de campo en Postgres para datos personales

**Estado:** Aceptada. Migración aplicada (18-oct-2026); validación formal pendiente de registrar. Versión pública resumida: se omiten inventario de workflows, pendientes y detalles de cliente.

## 1. Contexto

- Postgres guarda nombre y teléfono de pacientes, contexto de alertas (puede incluir datos de salud) y texto crudo de mensajes.
- Sin cifrado de campo, un dump o backup filtrado expone todo en claro.
- Amenazas a cubrir (confirmado): (a) dump o backup filtrado, (b) robo o compromiso de la laptop.
- Criterio rector: ante duda, mayor robustez (seguridad y confiabilidad).
- No hay datos reales de pacientes ni citas reales en producción (confirmado 28-sep-2026): no se requiere backfill. **Al llegar datos reales se agrega el backfill y se anota en `cupototal-contexto.md`.**

## 2. Decisión

### 2.1 Método

| Componente | Decisión |
|---|---|
| Cifrado reversible | AES-256-GCM en Code node de n8n. La llave nunca llega a Postgres. |
| Formato | `v1:<iv>:<tag>:<ciphertext>` (base64url) en una sola columna. El prefijo `v1` permite rotación futura (#10). |
| AAD | Cada valor se liga a su `tabla.columna`. Un valor cifrado pegado en otra columna falla al descifrar. |
| Búsqueda por teléfono | Índice ciego: HMAC-SHA256 del teléfono normalizado a sus últimos 10 dígitos. |
| Búsqueda por texto (dedup) | HMAC del texto (`contexto_hash`), porque un cifrado con IV aleatorio nunca es igual a otro. |
| Llaves | `CUPOTOTAL_ENC_KEY` y `CUPOTOTAL_HMAC_KEY` (32 bytes, en `$env`), distintas entre sí y de `N8N_ENCRYPTION_KEY`. Respaldadas fuera de la laptop (verificado 28-sep). |
| Código | Helper inline `crypto-helper v1`, copia canónica en `cupototal-workflows/snippets/`. La auditoría con `git grep` detecta copias desfasadas. Verificado: 15/15 pruebas en el contenedor n8n y smoke test en un Code node real. |
| Fallo | Cerrado. Llave ausente o valor no descifrable: se lanza error, nunca se escribe ni devuelve texto plano. Se registra como incidente (ADR-013: silencio al paciente). |
| Cifrado de disco | Se verifica en la laptop (amenaza b). Cubre `n8n_chat_histories` y lo que no entra al cifrado de campo. |
| Cambio de esquema | Dos fases: (1) **aditiva** (columnas nuevas, script `adr016-01-aditivo.sql`); (2) **corte** (eliminar columnas en claro y cambiar PKs), al final del Paso 5. |

### 2.2 Alcance por columna

| Tabla.columna | Tratamiento | Columnas nuevas |
|---|---|---|
| `citas_pacientes.nombre_paciente` | Cifrado reversible | `nombre_enc` |
| `citas_pacientes.telefono_paciente` | Cifrado reversible + HMAC | `telefono_enc`, `telefono_hash` |
| `fallback_alerts.telefono_paciente` | Cifrado reversible + HMAC | `telefono_enc`, `telefono_hash` |
| `fallback_alerts.contexto` | Cifrado reversible + HMAC (dedup) | `contexto_enc`, `contexto_hash` |
| `fallback_config.telefono_secretaria`, `telefono_admin` | Cifrado reversible | `*_enc` |
| `conversacion_estado.telefono_paciente` (PK) | Solo HMAC. La PK pasa a `telefono_hash` en el corte. | `telefono_hash` |
| `rate_limit_mensajes.telefono` | Solo HMAC | `telefono_hash` |
| `mensajes_buffer.telefono` | Solo HMAC (todas las consultas usan igualdad) | `telefono_hash` |
| `mensajes_buffer.texto` | Cifrado reversible | `texto_enc` |
| `processed_messages.message_id` | **Opción B:** HMAC del remitente, mismo comportamiento | `remitente_hash` |
| `configuracion_notificaciones.contacto_equipo` | Sin cambio hoy (sin referencias). Cuando se conecte, cifrado reversible. | — |
| `conversacion_estado.ultimo_mensaje_normalizado` | Sin escritor hoy (0 referencias). Cuando se use: texto del paciente, HMAC si solo se compara, cifrado si se lee. | — |

**Placeholder:** `"No disponible - búsqueda por nombre falló"` se guarda tal cual en `fallback_alerts.telefono_enc`, con `telefono_hash` en `NULL`. Los lectores llaman a `isPlaceholder()` antes de descifrar.

### 2.3 Texto libre (cerrado 29-sep, confirmado)

| Columna | Decisión | Razón |
|---|---|---|
| `auditoria_admin.detalles` | Texto plano + regla "solo IDs, nunca nombre ni teléfono del paciente" | Muestra sin datos personales. Cifrar dificulta leer la auditoría. |
| `incidentes_tecnicos.mensaje_error`, `stack_trace` | Texto plano | El mismo texto ya está en claro en `execution_data` (n8n). Cifrar la copia no reduce la exposición y agrega un punto de falla al manejador de errores. Se cubre con #13. |
| `bloqueos_calendario.motivo` | Texto plano | Ya vive en Google Calendar. |

Guardia de solo lectura (esperado 0/0/0; detecta números de 10 dígitos, **no** nombres; puede dar falsos positivos con timestamps en `stack_trace`):
`SELECT count(*) FROM auditoria_admin WHERE detalles ~ '[0-9]{10}'` y equivalentes.

### 2.4 Exclusiones explícitas

| Elemento | Razón |
|---|---|
| `admin_whitelist` | Camino crítico de autenticación con fail-closed probado. Son datos del staff. Riesgo de regresión alto con testing manual. Revisar cuando exista testing automatizado. |
| `n8n_chat_histories` | El nodo nativo escribe y lee en claro, y el LLM necesita el texto. Se cubre con cifrado de disco, purga de 90 días y #13/#14. |

### 2.5 `processed_messages`

- **[Hecho verificado]** Guarda el teléfono del remitente en `message_id`, no un ID de mensaje. La auditoría del 29-sep confirma que el nodo no tiene `alwaysOutputData` ni `onError` especial y que no hay IF entre él y `whitelist_admin`.
- **Contradicción abierta:** por diseño de n8n, cero filas cortaría el flujo, pero PM-035 exige que varios mensajes del mismo teléfono lleguen a `Check Pausado` (aguas abajo). **[No lo sé]** por qué no corta. Se investiga aparte.
- **Decisión:** B ahora (HMAC en `remitente_hash`, comportamiento idéntico). **C después, con ADR propio:** guardar el ID único del mensaje (`MessageSid` en Twilio; en Meta, otro identificador), que no es dato personal.

## 4. Casos borde y decisiones de diseño

- **Un solo nodo de hash en la entrada del principal** (`telefono_hash` una vez; lo reutilizan rate limit, buffer, `processed_messages` y `Check Pausado`).
- **Code nodes de cifrado en modo "Run Once for Each Item".** **[Inferencia]** En modo "todos los items" se puede perder el emparejamiento (`pairedItem`), como el bug de `Merge` de PM-037. Se prueba en el Paso 5.
- **Recordatorios 24h corren sin supervisión:** un fallo de descifrado en una cita no debe tumbar el lote. Falla visible por cita, no silenciosa.
- **Migración expandir → migrar → contraer (sustituye al "corte sincronizado" del borrador):** durante la migración cada workflow escribe la columna vieja y la nueva, y lee por la nueva; las columnas en claro se eliminan al final tras un re-audit.
- **Nota sobre PK (probado en réplica):** con la PK vieja activa, un upsert solo por hash falla por `NOT NULL`; por eso se escribe también la columna vieja hasta el contraer.
- (Texto original, conservado como contexto) **Corte sincronizado en tablas con PK afectada** (`conversacion_estado`, `processed_messages`): con la PK vieja activa, un upsert por hash falla por `NOT NULL`. **Probado en réplica local (Postgres 16.15, tablas reconstruidas desde el inventario), no en la BD real.** La PK cambia en el mismo momento que los workflows.
- Dedup de `fallback_alerts` por `telefono_hash` (rama real) y `contexto_hash` (rama placeholder): probado en la réplica.
- `Reset Conversacion Estado` (`NOT EXISTS ... = $1` sin `REPLACE`): **[Inferencia]** probable falsa alarma, porque `$1` sale de la misma columna de `fallback_alerts`. Se prueba en el Paso 5 con dos alertas activas del mismo paciente: al resolver una, debe seguir pausado. El HMAC sobre 10 dígitos elimina esta clase de desajuste de formato.
- Teléfono en formatos `whatsapp:+521...`, `+52...` o sin prefijo: el HMAC siempre sale del valor normalizado.
- Valor `NULL` o vacío: no se cifra ni rompe el nodo.
- Llave ausente: falla cerrado, sin escribir texto plano.

## 5. Consecuencias y límites

| Límite | Efecto |
|---|---|
| El nombre del paciente también vive en Google Calendar (`summary`) y vivirá en Sheets (Ola 5.2) | Se protege solo la copia de Postgres. |
| n8n guarda Input/Output de las ejecuciones en claro (`execution_data.data`) | No cubierto aquí. Ítem #13. |
| La llave vive en `$env`, legible por todos los Code nodes | Trade-off ya aceptado. |
| Perder la llave significa perder los datos | Respaldo verificado (28-sep). Agregar a `RUNBOOK-DR.md` (no probado en ensayo). |
| Consultas manuales (ARCO, `docker exec psql`) ya no buscan por teléfono directo | Script en Python para HMAC y descifrado. Actualizar la sec. 4 de `POLITICA-RETENCION-ARCO.md` (frase "como hash" → HMAC). |
| Tablas futuras (`recordatorios_enviados`, `costos_conversacion`, columna `tipo`) | Deben seguir este ADR al crearse en Ola 5.2. |
| Muchos nodos tocados con testing manual | Se mitiga con casos borde explícitos y pruebas en el Paso 5. |

