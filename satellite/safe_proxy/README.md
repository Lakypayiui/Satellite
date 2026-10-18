# Safe Proxy (embebido en Satellite)

Proxy local entre tus herramientas y la API de DeepSeek (o cualquier API compatible con
OpenAI). Sanea lo que sale, restaura lo que vuelve y deja auditoría sin contenido.

**Ahora vive dentro de Satellite** (``satellite/safe_proxy/``) y se controla desde
**Ajustes → Safe Proxy**, con dos modos y el mismo código detrás (``SafeProxyCore``):

- **``internal`` (por defecto)**: no abre **ningún puerto**. La política se aplica dentro del
  propio Satellite, en el ciclo de la petición al proveedor (``interceptor.py``): se bloquea,
  ofusca y enmascara **antes de enviar** y se restauran los marcadores en la respuesta. No hay
  HTTP, ni loopback, ni roundtrip. Su interruptor (``internal_enabled``) viene **apagado**:
  activarlo es un clic y con él apagado la petición pasa intacta (bypass).
- **``server`` (opcional)**: el mismo servicio expuesto en **loopback** para consumidores
  externos, con su API de control autenticada y start/stop reales. No arranca solo.

Ningún fichero externo, ninguna carpeta hermana y ningún proceso aparte.

## Qué hace

1. **Bloquea** (nada sale) si hay términos de `block_terms`, coincidencias de `block_regex`
   o secretos (llaves, tokens, contraseñas).
2. **Ofusca Python**: renombra funciones, clases, argumentos y variables propias a `obf_N`;
   elimina comentarios y docstrings.
3. **Enmascara** términos de `mask_terms` y PII (correo, teléfono, CURP, RFC, CLABE,
   tarjeta con Luhn, IP) como `<TERM_1>`, `<EMAIL_1>`…
4. **Restaura** los marcadores en la respuesta antes de devolvérsela al cliente.
5. **Audita** en `audit.jsonl` solo conteos y un hash, nunca contenido.
6. **Falla cerrado**: ante cualquier error no se envía nada.

Además: solo escucha en loopback, exige token, no reenvía tools ni imágenes, y solo deja
pasar los modelos de `allowed_models`.

## Ciclo de vida (Ajustes → Safe Proxy)

- **Encender / Apagar**: arranca o para el servicio embebido (in-process, `uvicorn` sobre un
  socket propio). Al apagar deja de escuchar y **libera el puerto**; al encender vuelve con
  el estado y la configuración guardados.
- **Escucha**: host fijo loopback (`127.0.0.1`) + puerto configurable (1-65535).
- **Estado real**: `parado` / `en marcha` / `error de configuración` / `puerto ocupado`.
- **Opciones** (allowlist): modelos permitidos, términos bloqueados/enmascarados, PII,
  acción ante secretos, banderas de código y `upstream.timeout_seconds`. Se aplican en
  caliente y se persisten.

## Dónde vive su estado

Bajo `<SATELLITE_HOME>` (por defecto `~/.satellite`):

| Ruta | Contenido |
| --- | --- |
| `safe_proxy.json` | host/puerto y **tokens** (acceso y control), `0600`, write-only |
| `safe_proxy/config.json` | configuración base (generada desde `defaults.py` la 1ª vez) |
| `safe_proxy/state.json` | `enabled` + opciones allowlisted persistidas (nunca secretos) |
| `safe_proxy/audit.jsonl` | auditoría sin contenido (conteos y hash) |

## API de control local

API mínima para que la app de escritorio lea el estado, ajuste opciones permitidas y
habilite/deshabilite el routing. Pensada para **loopback**.

Reglas de seguridad:

- Solo escucha si el host es **loopback** (siempre lo es aquí).
- Usa un **token propio**, distinto del token de acceso, comparado en tiempo constante
  (`hmac.compare_digest`) y **nunca devuelto**.
- Todas las respuestas van **redactadas**: solo nombres de variables de entorno y
  booleanos (`api_key_configured`), jamás valores de tokens ni de la api key.
- El `PATCH` no puede tocar bind, puerto, rutas ni secretos, y no hay peticiones salientes
  ni seguimiento de redirects (sin SSRF).

Autenticación: cabecera `Authorization: Bearer <token de control>` (o `X-Control-Token`).

| Método | Ruta | Descripción |
| --- | --- | --- |
| GET | `/control/status` | `enabled`, control disponible, timeout, modelos permitidos |
| GET | `/control/config` | Config efectiva redactada (server/upstream/policy/code, sin secretos) |
| PATCH | `/control/config` | Actualiza la allowlist (ver abajo) |
| POST | `/control/enable` | Rehabilita el routing del proxy |
| POST | `/control/disable` | Deshabilita el routing: el proxy responde **503** (el control sigue vivo) |

Allowlist del `PATCH` (claves permitidas; cualquier otra → `400`):

```json
{
  "policy": {
    "allowed_models": ["deepseek-chat"],
    "block_terms": ["Proyecto Orion"],
    "mask_terms": ["Acme Corp"],
    "pii": ["EMAIL", "PHONE", "CURP", "RFC", "CLABE", "CARD", "IP"],
    "secrets_action": "block",
    "api_keys": true
  },
  "code": {
    "obfuscate_python": true,
    "strip_comments": true,
    "other_languages": "block",
    "fail_closed": true
  },
  "upstream": { "timeout_seconds": 180 }
}
```

## Límites (léelos)

- El proveedor **sí ve** la lógica ofuscada. Renombrar no oculta la estructura del
  algoritmo; si la estructura es el secreto, usa un modelo local.
- Solo se ofusca código Python dentro de bloques con ``` (o un mensaje que sea todo
  código). Código suelto mezclado con prosa o inline no se protege: usa siempre bloques.
- Otros lenguajes se **bloquean** por defecto (`code.other_languages`).
- Las cadenas de texto dentro del código no se ofuscan (solo `mask_terms`/PII).
- La detección de PII es por patrones: puede haber falsos negativos.
- El streaming es simulado: el proxy espera la respuesta completa, la restaura y la emite
  como SSE.
- Si el modelo altera un marcador (`obf_3`, `<TERM_1>`), no se puede restaurar.

## Pruebas

```bash
python -m pytest tests/test_safe_proxy_service.py tests/test_safe_proxy_control.py \
  tests/test_safe_proxy_persistence.py tests/test_safe_proxy_integration.py -q
```

Origen y licencia: ver `NOTICE.md`.
