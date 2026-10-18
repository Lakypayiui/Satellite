"""Detección central de archivos sensibles (secretos) para indexar y construir contexto.

Un archivo sensible no se indexa, no se ofrece como @mención y no se lee como
contexto por defecto: así no se filtra a ``index.json`` ni a los prompts. La
coincidencia es por **nombre preciso** (no por subcadena), para no marcar como
secreto un archivo que sólo se llama parecido (p. ej. ``secreto.txt``).

Los patrones que ya usaba el índice (``.env`` y nombres con «secret»/«credential»)
siguen aplicándose vía :func:`is_index_excluded`, de modo que la exclusión previa
se conserva.
"""

from __future__ import annotations

from pathlib import Path

# Nombres exactos (en minúsculas) que nunca deben tratarse como contenido normal.
SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".envrc",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".pgpass",
        ".htpasswd",
        ".git-credentials",
        ".dockercfg",
        "credentials",
        "credentials.json",
        "credentials.yml",
        "credentials.yaml",
        ".credentials",
        ".credentials.json",
        "secrets",
        "secrets.json",
        "secrets.yml",
        "secrets.yaml",
        ".secrets",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "kubeconfig",
    }
)
# Prefijos de nombre (p. ej. ``.env.local``, ``.env.production``).
SENSITIVE_PREFIXES = (".env.",)
# Sufijos que delatan llaves o almacenes de claves.
SENSITIVE_SUFFIXES = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".ppk"}
)
# Fragmentos que ya excluía el índice (nombres que los contienen). Se conservan
# para no perder cobertura previa; no se usan al leer menciones (evita falsos
# positivos como «secreto»).
SENSITIVE_FRAGMENTS = ("secret", "credential")


def is_sensitive_name(name: object) -> bool:
    """True si el nombre del archivo delata un secreto (coincidencia precisa)."""
    low = str(name or "").strip().lower()
    if not low:
        return False
    if low in SENSITIVE_NAMES or any(low.startswith(prefijo) for prefijo in SENSITIVE_PREFIXES):
        return True
    return Path(low).suffix in SENSITIVE_SUFFIXES


def is_sensitive_path(path: object) -> bool:
    """True si la ruta apunta a un archivo sensible (por su nombre)."""
    return is_sensitive_name(Path(path).name)


def is_index_excluded(path: object) -> bool:
    """Exclusión al indexar: la precisa más los fragmentos que ya se excluían."""
    name = Path(path).name.lower()
    if is_sensitive_name(name):
        return True
    return any(fragmento in name for fragmento in SENSITIVE_FRAGMENTS)
