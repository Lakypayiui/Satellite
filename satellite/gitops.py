"""Operaciones Git y worktrees para Satellite: seguras, sin shell y con rutas validadas.

Todo se ejecuta con ``argv`` (nunca ``shell=True``), con timeout y entorno controlado. Lo que
existe aquí es lo que la app puede afirmar: **estado real**, ``stage``/``unstage``, ``commit``
explícito y el ciclo de worktrees.

Nada destructivo o remoto: no hay ``push``, ``merge``, ``reset --hard`` ni ``clean``. Quitar un
worktree con cambios sin confirmar se rechaza salvo ``force`` explícito.

Worktrees: se crean bajo ``<proyecto>/.satellite/worktrees/<slug>`` (dentro del proyecto, así la
política de rutas del runtime los acepta) y el *contexto de ejecución* activo se guarda en
``<proyecto>/.satellite/state/execution.json``. El runtime lo lee al armarse y ejecuta las tools
con ese ``workdir``, de modo que el agente escribe en el worktree y **no** en el workspace
original mientras esté activo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

GIT_TIMEOUT_S = 30
WORKTREE_TIMEOUT_S = 120
WORKTREES_DIR = Path(".satellite") / "worktrees"
EXECUTION_FILE = Path(".satellite") / "state" / "execution.json"
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UNMERGED = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


class GitError(RuntimeError):
    """Fallo de git con un mensaje ya legible (no un stacktrace)."""


def _git_env() -> dict[str, str]:
    """Entorno para invocar ``git`` en nombre del usuario (con HOME/USERPROFILE).

    Se añade ``GIT_TERMINAL_PROMPT=0`` para que nunca se quede esperando credenciales en una
    operación no interactiva.
    """
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    return env


def _git(
    root: Path,
    args: list[str],
    *,
    timeout_s: int = GIT_TIMEOUT_S,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Ejecuta ``git <args>`` en ``root`` (sin shell).

    Sin ``check`` no lanza: devuelve el proceso para que quien llame decida. Si git no está
    instalado, ``FileNotFoundError`` se traduce a ``GitError`` con un mensaje útil.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            env=_git_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise GitError("git no está instalado o no está en el PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git tardó demasiado (>{timeout_s}s): {' '.join(args)}") from exc
    if check and proc.returncode != 0:
        detalle = (proc.stderr or proc.stdout or "").strip() or f"git {' '.join(args)}"
        raise GitError(detalle)
    return proc


def is_repo(root: Path | str) -> bool:
    """True si ``root`` está dentro de un árbol de trabajo git."""
    try:
        proc = _git(Path(root), ["rev-parse", "--is-inside-work-tree"])
    except GitError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _require_repo(root: Path) -> None:
    """Exige un repo; si git no está instalado, propaga ese error (más útil)."""
    proc = _git(Path(root), ["rev-parse", "--is-inside-work-tree"])
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise GitError(f"no es un repositorio git: {root}")


# ----------------------------------------------------------------- estado


def _parse_porcelain(raw: str) -> list[dict]:
    """Convierte ``git status --porcelain=v1 -z`` en entradas normalizadas."""
    tokens = [token for token in raw.split("\0") if token]
    entradas: list[dict] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if len(token) < 3:
            continue
        code, path = token[:2], token[3:]
        if code[0] in ("R", "C"):
            # Renames/copies traen el origen como token siguiente (sin código).
            if i < len(tokens):
                origen = tokens[i]
                i += 1
                path = f"{origen} -> {path}"
        x, y = code[0], code[1]
        entradas.append(
            {
                "path": path,
                "code": code,
                "staged": code in UNMERGED or x not in " ?",
                "unstaged": code in UNMERGED or y not in " ?",
                "untracked": code == "??",
                "conflicted": code in UNMERGED,
            }
        )
    return entradas


def _ahead_behind(root: Path) -> tuple[int, int]:
    """``(ahead, behind)`` respecto al upstream.

    ``git rev-list --left-right --count @{u}...HEAD`` da ``izquierda=behind`` (commits que
    sólo están en el upstream) y ``derecha=ahead`` (los que sólo están en HEAD).
    """
    proc = _git(root, ["rev-list", "--left-right", "--count", "@{u}...HEAD"])
    if proc.returncode != 0:
        return 0, 0
    partes = proc.stdout.split()
    if len(partes) != 2:
        return 0, 0
    try:
        behind, ahead = int(partes[0]), int(partes[1])
    except ValueError:
        return 0, 0
    return ahead, behind


def status(root: Path | str) -> dict:
    """Estado real del repo: rama, upstream, ahead/behind, cambios y conflictos."""
    base = Path(root)
    _require_repo(base)
    branch = _git(base, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    upstream = _git(
        base, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    ).stdout.strip()
    ahead, behind = _ahead_behind(base) if upstream else (0, 0)
    entradas = _parse_porcelain(_git(base, ["status", "--porcelain=v1", "-z"]).stdout)
    return {
        "is_repo": True,
        "branch": branch,
        "detached": branch == "HEAD",
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "clean": not entradas,
        "files": entradas,
        "staged": [
            e for e in entradas if e["staged"] and not e["untracked"] and not e["conflicted"]
        ],
        "unstaged": [
            e for e in entradas if e["unstaged"] and not e["untracked"] and not e["conflicted"]
        ],
        "untracked": [e for e in entradas if e["untracked"]],
        "conflicts": [e for e in entradas if e["conflicted"]],
    }


# ------------------------------------------------------------- stage / commit


def _validate_paths(root: Path, paths: list[str]) -> list[str]:
    """Normaliza a rutas relativas al repo y rechaza escapes/absolutas."""
    base = root.resolve()
    limpios: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            raise GitError("ruta vacía")
        rel = Path(raw)
        if rel.is_absolute():
            raise GitError(f"la ruta debe ser relativa al repo: {raw}")
        resuelta = (base / rel).resolve()
        if resuelta != base and base not in resuelta.parents:
            raise GitError(f"la ruta queda fuera del repo: {raw}")
        limpios.append(rel.as_posix())
    return limpios


def stage(root: Path | str, paths: list[str]) -> dict:
    """Marca rutas para el próximo commit (``git add``). Nunca borra nada."""
    base = Path(root)
    _require_repo(base)
    rel = _validate_paths(base, paths)
    proc = _git(base, ["add", "--", *rel])
    return {
        "ok": proc.returncode == 0,
        "staged": rel,
        "error": (proc.stderr or "").strip() if proc.returncode != 0 else "",
    }


def unstage(root: Path | str, paths: list[str]) -> dict:
    """Saca rutas del área de stage (``git restore --staged``)."""
    base = Path(root)
    _require_repo(base)
    rel = _validate_paths(base, paths)
    proc = _git(base, ["restore", "--staged", "--", *rel])
    return {
        "ok": proc.returncode == 0,
        "unstaged": rel,
        "error": (proc.stderr or "").strip() if proc.returncode != 0 else "",
    }


def commit(root: Path | str, message: str) -> dict:
    """Crea un commit con el mensaje dado. Falla en claro si git no tiene identidad.

    No hace push ni toca remotos: es exactamente ``git commit -m``.
    """
    base = Path(root)
    _require_repo(base)
    texto = (message or "").strip()
    if not texto:
        raise GitError("el mensaje de commit no puede estar vacío")
    proc = _git(base, ["commit", "-m", texto], timeout_s=60)
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "output": (proc.stdout or "").strip(),
        "error": (proc.stderr or "").strip() if not ok else "",
    }


# ------------------------------------------------------------------ worktrees


def worktrees_dir(root: Path | str) -> Path:
    """Carpeta gestionada donde viven los worktrees (dentro del proyecto)."""
    return Path(root).resolve() / WORKTREES_DIR


def list_worktrees(root: Path | str) -> list[dict]:
    """Worktrees registrados del repo (incluye el principal)."""
    base = Path(root)
    if not is_repo(base):
        return []
    proc = _git(base, ["worktree", "list", "--porcelain"])
    if proc.returncode != 0:
        return []
    salida: list[dict] = []
    actual: dict = {}
    for linea in proc.stdout.splitlines():
        if not linea.strip():
            if actual:
                salida.append(_cerrar_worktree(actual))
                actual = {}
            continue
        clave, _, valor = linea.partition(" ")
        if clave == "worktree":
            actual["path"] = valor.strip()
        elif clave == "branch":
            actual["branch"] = valor.strip().replace("refs/heads/", "")
        elif clave == "HEAD":
            actual["head"] = valor.strip()
        elif clave == "detached":
            actual["detached"] = True
        elif clave == "locked":
            actual["locked"] = True
        elif clave == "prunable":
            actual["prunable"] = True
    if actual:
        salida.append(_cerrar_worktree(actual))
    gestionado = worktrees_dir(base)
    for item in salida:
        # Git puede devolver separadores "/" también en Windows; se normaliza para que
        # comparar con `active_workdir` (Path.resolve) funcione en las tres superficies.
        try:
            ruta = Path(item["path"]).resolve()
            item["path"] = str(ruta)
        except OSError:  # pragma: no cover - ruta rara del sistema
            ruta = Path(item["path"])
        item["managed"] = gestionado in ruta.parents
        item["main"] = ruta == base.resolve()
        # Estado del worktree gestionado: quitar uno con cambios no debe ser a ciegas.
        item["dirty"] = bool(
            item["managed"] and not item["main"] and ruta.is_dir() and worktree_dirty(ruta)
        )
    return salida


def _cerrar_worktree(parcial: dict) -> dict:
    return {
        "path": str(parcial.get("path", "")),
        "branch": str(parcial.get("branch", "")),
        "head": str(parcial.get("head", "")),
        "detached": bool(parcial.get("detached", False)),
        "locked": bool(parcial.get("locked", False)),
        "prunable": bool(parcial.get("prunable", False)),
    }


def worktree_dirty(path: Path | str) -> bool:
    """True si el worktree tiene cambios sin confirmar (o no se puede leer)."""
    base = Path(path)
    if not base.is_dir() or not is_repo(base):
        return False
    try:
        proc = _git(base, ["status", "--porcelain"])
    except GitError:
        return True  # no se pudo comprobar: no se afirma que esté limpio
    if proc.returncode != 0:
        return True  # no se pudo comprobar: no se afirma que esté limpio
    return bool(proc.stdout.strip())


def add_worktree(
    root: Path | str, name: str, *, branch: str = "", base_ref: str = ""
) -> dict:
    """Crea un worktree gestionado con una rama nueva. Devuelve su ruta y rama."""
    base = Path(root)
    _require_repo(base)
    slug = (name or "").strip()
    if not SLUG_RE.match(slug):
        raise GitError(
            "nombre inválido: usa letras, números, punto, guion o guion bajo (máx. 64)"
        )
    destino = worktrees_dir(base) / slug
    if destino.exists():
        raise GitError(f"ya existe un worktree en {destino}")
    rama = (branch or "").strip() or f"wt/{slug}"
    if _branch_exists(base, rama):
        raise GitError(f"la rama {rama!r} ya existe; elige otro nombre")
    origen = (base_ref or "").strip() or "HEAD"
    destino.parent.mkdir(parents=True, exist_ok=True)
    proc = _git(
        base,
        ["worktree", "add", "-b", rama, "--", str(destino), origen],
        timeout_s=WORKTREE_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout or "").strip() or "git worktree add falló")
    return {"path": str(destino), "branch": rama, "base": origen}


def _branch_exists(root: Path, branch: str) -> bool:
    proc = _git(root, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
    return proc.returncode == 0


def remove_worktree(root: Path | str, path: str, *, force: bool = False) -> dict:
    """Quita un worktree gestionado. Rechaza si tiene cambios sin confirmar (salvo ``force``)."""
    base = Path(root)
    _require_repo(base)
    objetivo = Path(path).expanduser().resolve()
    gestionado = worktrees_dir(base)
    if objetivo != gestionado and gestionado not in objetivo.parents:
        raise GitError("sólo se pueden quitar worktrees gestionados por Satellite")
    if objetivo == base.resolve():
        raise GitError("no se puede quitar el worktree principal")
    if not objetivo.exists():
        _git(base, ["worktree", "prune"])  # limpia registros de worktrees que ya no están
        return {"removed": str(objetivo), "already": True}
    if not force and worktree_dirty(objetivo):
        raise GitError(
            "el worktree tiene cambios sin confirmar; vuelve a pedirlo con force para descartarlos"
        )
    args = ["worktree", "remove", str(objetivo)]
    if force:
        args.append("--force")
    proc = _git(base, args, timeout_s=WORKTREE_TIMEOUT_S)
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout or "").strip() or "git worktree remove falló")
    _git(base, ["worktree", "prune"])
    return {"removed": str(objetivo)}


# ------------------------------------------------------- contexto de ejecución


def _read_execution_file(root: Path) -> dict:
    path = Path(root).resolve() / EXECUTION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def active_workdir(root: Path | str) -> Path | None:
    """Workdir de ejecución activo, o ``None`` si se ejecuta sobre el proyecto.

    Sólo se acepta si sigue existiendo y es un directorio dentro del proyecto o un worktree
    registrado; si no, se ignora (nunca se ejecuta "en" una carpeta que ya no está).
    """
    base = Path(root).resolve()
    raw = str(_read_execution_file(base).get("workdir") or "").strip()
    if not raw:
        return None
    objetivo = Path(raw).expanduser()
    if not objetivo.is_absolute():
        objetivo = base / objetivo
    if not objetivo.is_dir():
        return None
    resuelto = objetivo.resolve()
    if resuelto == base or base in resuelto.parents:
        return resuelto
    for item in list_worktrees(base):
        try:
            if item["path"] and Path(item["path"]).resolve() == resuelto:
                return resuelto
        except OSError:
            continue
    return None


def set_active_workdir(root: Path | str, path: str | None) -> Path | None:
    """Fija (o limpia con ``None``/``""``) el workdir de ejecución. Devuelve el efectivo.

    Valida que la ruta exista y sea el proyecto, una carpeta dentro de él o un worktree
    registrado: no se apunta la ejecución a una carpeta arbitraria del sistema.
    """
    base = Path(root).resolve()
    archivo = base / EXECUTION_FILE
    objetivo = (path or "").strip()
    if not objetivo:
        _write_json(archivo, {"workdir": ""})
        return None
    candidato = Path(objetivo).expanduser()
    if not candidato.is_absolute():
        candidato = base / candidato
    resuelto = candidato.resolve()
    if not resuelto.is_dir():
        raise GitError(f"el workdir no existe o no es una carpeta: {resuelto}")
    dentro = resuelto == base or base in resuelto.parents
    es_worktree = False
    if not dentro and is_repo(base):
        for item in list_worktrees(base):
            try:
                if item["path"] and Path(item["path"]).resolve() == resuelto:
                    es_worktree = True
                    break
            except OSError:
                continue
    if not dentro and not es_worktree:
        raise GitError("el workdir debe estar dentro del proyecto o ser un worktree registrado")
    _write_json(archivo, {"workdir": str(resuelto)})
    return resuelto


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
