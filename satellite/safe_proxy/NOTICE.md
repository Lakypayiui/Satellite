# Safe Proxy — origen y licencia

Este paquete (``satellite/safe_proxy/``) es una **copia vendorizada** del servicio
``safe_proxy.py``, integrada dentro de Satellite para que funcione **embebida** (arrancada
en proceso por el backend de escritorio), sin depender de ninguna carpeta externa en
tiempo de ejecución.

## Procedencia

- Origen: proyecto local ``safe-proxy`` (``safe_proxy.py``, ``config.yaml``, ``README.md``
  y su suite de pruebas), facilitado por el autor como **fuente** para esta integración.
- Fecha de integración: 2026-10-17.
- Módulos portados:
  - ``sanitizer.py`` ← el saneador y los detectores (lógica pura, sin cambios de
    comportamiento).
  - ``service.py`` ← el servicio (``FastAPI``) y su API de control, convertido de módulo
    con estado global a **clase instanciable** (config/estado/saneador por instancia).
  - ``defaults.py`` / ``config.yaml`` ← la configuración base (el runtime usa JSON para no
    depender de PyYAML; el YAML se conserva como plantilla/documentación).
  - ``README.md`` ← documentación de origen, adaptada al modo embebido.
- Pruebas portadas a ``tests/test_safe_proxy_service.py``,
  ``tests/test_safe_proxy_control.py`` y ``tests/test_safe_proxy_persistence.py``.

## Licencia

El proyecto de origen **no incluye ningún archivo de licencia ni aviso de copyright**
(no hay ``LICENSE``, ``COPYING`` ni cabeceras de licencia en ``safe_proxy.py``). Por tanto
no hay un texto de licencia que conservar; se incluye aquí el aviso de procedencia y se
mantiene la atribución al autor original. Si el autor añade una licencia más adelante,
debe copiarse a este directorio junto con este aviso.

## Cambios respecto al origen

- Host **forzado a loopback** (``127.0.0.1``); el bind no se acepta del cliente.
- Tokens de acceso y control **generados por Satellite** y persistidos en
  ``<SATELLITE_HOME>/safe_proxy.json`` (``0600``), separados y **write-only** para la
  interfaz.
- Config/estado/auditoría en ``<SATELLITE_HOME>/safe_proxy/``; el estado se escribe de
  forma atómica y nunca guarda tokens ni secretos.
- Configuración en **JSON** (``config.json``) para evitar la dependencia de PyYAML.
- Ciclo de vida **in-process** (``uvicorn`` sobre un socket propio) en lugar de un proceso
  externo; al parar se libera el puerto y se reportan ``stopped`` /
  ``running`` / ``config-error`` / ``port-conflict``.
