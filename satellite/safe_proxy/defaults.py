"""Configuración base del servicio embebido (equivalente a ``config.yaml`` del origen).

Se usa como plantilla al crear ``<SATELLITE_HOME>/safe_proxy/config.json`` y como base
sobre la que se aplica el estado persistido (``state.json``). El host es siempre loopback:
el servicio embebido no escucha en otra interfaz.
"""

from __future__ import annotations

DEFAULT_CONFIG: dict = {
    "upstream": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "timeout_seconds": 180,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8787,
        "max_body_bytes": 300_000,
        "audit_log": "audit.jsonl",
        "state_file": "state.json",
    },
    "policy": {
        "allowed_models": ["deepseek-chat", "deepseek-reasoner"],
        "allow_tools": False,
        "block_terms": [],
        "block_regex": [
            "(?i)\\bconfidencial\\b",
            "(?i)\\bconfidential\\b",
            "(?i)all rights reserved",
            "(?i)todos los derechos reservados",
        ],
        "secrets_action": "block",
        # Detección de claves de API (sk-…, AIza…, xai-…, hf_…, gsk_…, r8_…, pplx-…):
        # activa por defecto (fail-safe). Apagarla deja pasar esas claves sin tocar.
        "api_keys": True,
        "mask_terms": [],
        "pii": ["EMAIL", "PHONE", "CURP", "RFC", "CLABE", "CARD", "IP"],
    },
    "code": {
        "obfuscate_python": True,
        "strip_comments": True,
        "other_languages": "block",
        "fail_closed": True,
    },
}
