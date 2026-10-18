"""Safe Proxy **embebido** en Satellite (ya no depende de ninguna carpeta externa).

El servicio de saneamiento (bloquea → ofusca → enmascara → reenvía → restaura → audita,
siempre fail-closed) vive aquí y lo arranca/para el backend de escritorio **en proceso**,
sin ``Popen`` ni rutas hermanas:

- ``sanitizer``: lógica pura del saneador (detectores, ofuscación, pipeline).
- ``service``:   el servicio (app ASGI) con su config, estado y API de control.
- ``manager``:   el ciclo de vida in-process (arrancar/parar, puerto, estados).
- ``config``:    configuración del host (host/puerto + tokens write-only) y la allowlist.
- ``client``:    cliente HTTP del control local (loopback, sin redirects).
- ``defaults``:  config base que se copia a ``<SATELLITE_HOME>/safe_proxy/config.json``.

Los datos del servicio viven en ``<SATELLITE_HOME>/safe_proxy/`` (config/estado/auditoría).
Procedencia y licencia: ver ``NOTICE.md`` (y ``README.md``/``config.yaml`` de origen).
"""
