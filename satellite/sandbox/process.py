"""Ejecución de subprocesos con timeout real y terminación de todo el árbol.

Un timeout que sólo mata al hijo directo deja vivos a sus descendientes: el comando
"termina" pero lo que bloqueaba (un hijo, un nieto que heredó las tuberías) sigue
corriendo y puede retener la salida. Aquí el proceso se arranca en su **propio grupo
/sesión** y, cuando se agota el timeout o se cancela, se termina **todo el árbol**
antes de devolver.

La lectura de la salida nunca decide el timeout: ``communicate`` va bajo ``wait_for``
y, si el timeout gana, no se vuelve a esperar salida (eso volvería a colgarse con un
descendiente que mantiene las tuberías abiertas); se mata, se reapa y se devuelve.

Es el punto ÚNICO que ejecuta un proceso con timeout: ``tools/terminal.py`` y
``sandbox/subprocess_sandbox.py`` lo comparten, y ``tools/jobs.py`` usa su variante
síncrona para parar un trabajo con descendientes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"
# Margen (POSIX) entre SIGTERM al grupo y el SIGKILL forzado.
KILL_GRACE_S = 1.0
# Tope para reapa al hijo tras la muerte forzada (no puede quedar sin reapa).
REAP_TIMEOUT_S = 5.0

__all__ = [
    "IS_WINDOWS",
    "KILL_GRACE_S",
    "REAP_TIMEOUT_S",
    "ProcessOutcome",
    "group_kwargs",
    "platform_argv",
    "terminate_process_tree",
    "terminate_tree_sync",
    "run_process",
]


@dataclass(frozen=True)
class ProcessOutcome:
    """Resultado crudo de un proceso, tipado y sin ambigüedad."""

    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    cancelled: bool = False


def group_kwargs() -> dict:
    """Arranca el proceso en su propio grupo/sesión para poder matar al árbol.

    Requisito de ``terminate_process_tree``: en POSIX se manda la señal al **grupo**
    (``killpg``) y para eso el hijo debe ser líder de grupo (``start_new_session``); en
    Windows se crea un grupo nuevo (``CREATE_NEW_PROCESS_GROUP``) para que ``taskkill``
    con ``/T`` recorra el árbol con un límite claro.
    """
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True}


def platform_argv(argv: list[str] | tuple[str, ...]) -> list[str]:
    """Adapt a command for the host: Windows cannot execute .cmd/.bat directly."""
    result = [str(part) for part in argv]
    if IS_WINDOWS and result:
        suffix = Path(result[0]).suffix.lower()
        if suffix in (".cmd", ".bat"):
            return [
                os.environ.get("ComSpec", "cmd.exe"),
                "/d",
                "/s",
                "/c",
                subprocess.list2cmdline(result),
            ]
    return result


def _signal_group(pid: int, sig: int) -> None:
    """Señal al grupo de ``pid`` (silencioso si ya no existe o no hay permiso)."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


async def _taskkill(pid: int, *, force: bool) -> None:
    """``taskkill`` del árbol en Windows; sin excepción si no se puede."""
    args = ["taskkill", "/T", "/PID", str(pid)]
    if force:
        args.insert(1, "/F")
    try:
        killer = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(killer.wait(), timeout=REAP_TIMEOUT_S)


async def _reap(proc, timeout: float) -> bool:
    """Espera a que ``proc`` muera; ``False`` si no lo hizo a tiempo."""
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def terminate_process_tree(proc) -> None:
    """Termina ``proc`` y sus descendientes, y lo reapa. Tolerante e idempotente.

    En POSIX se manda SIGTERM al grupo, se da un margen y, si no cede, SIGKILL. En
    Windows se usa ``taskkill /F /T`` directamente: ``taskkill`` sin ``/F`` no termina de
    forma fiable un proceso de consola, y el objetivo es terminarlo **de verdad** (y a su
    árbol). Aun cuando el hijo directo ya haya muerto se intenta el grupo/árbol: los
    descendientes pueden seguir vivos y heredaron las tuberías.
    """
    if IS_WINDOWS:
        await _taskkill(proc.pid, force=True)
    else:
        _signal_group(proc.pid, signal.SIGTERM)
        if proc.returncode is None and await _reap(proc, KILL_GRACE_S):
            return
        _signal_group(proc.pid, signal.SIGKILL)

    # Último recurso: matar al propio hijo y garantizar el reapa.
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()
    if proc.returncode is None:
        await _reap(proc, REAP_TIMEOUT_S)


def _taskkill_sync(pid: int, *, force: bool) -> None:
    args = ["taskkill", "/T", "/PID", str(pid)]
    if force:
        args.insert(1, "/F")
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=REAP_TIMEOUT_S,
        )


def terminate_tree_sync(proc: "subprocess.Popen", *, grace: float = KILL_GRACE_S) -> None:
    """Variante síncrona para ``subprocess.Popen`` (jobs en segundo plano).

    Misma semántica que :func:`terminate_process_tree`: en POSIX SIGTERM al grupo y, si
    no cede, SIGKILL; en Windows ``taskkill /F /T``. Termina también a los descendientes.
    """
    if IS_WINDOWS:
        _taskkill_sync(proc.pid, force=True)
    else:
        _signal_group(proc.pid, signal.SIGTERM)
        if proc.poll() is None:
            try:
                proc.wait(timeout=grace)
                return
            except subprocess.TimeoutExpired:
                pass
        _signal_group(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=REAP_TIMEOUT_S)


async def run_process(
    argv: list[str],
    *,
    cwd,
    env: dict,
    timeout_s: float,
    stdin: str | None = None,
    cancel=None,
) -> ProcessOutcome:
    """Ejecuta ``argv`` sin shell con timeout real y cancelación cooperativa.

    Lanza ``OSError``/``ValueError`` si el proceso no se puede crear (el llamador lo
    traduce a un resultado tipado). Nunca se queda colgado en la lectura de salida: el
    ``communicate`` está bajo ``wait_for`` y, al ganar el timeout o la cancelación, se
    termina el árbol sin volver a leer.
    """
    from satellite.orchestration.cancel import race_cancel

    argv = platform_argv(argv)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **group_kwargs(),
    )
    payload = stdin.encode("utf-8") if stdin is not None else None
    try:
        salida, cancelado = await race_cancel(
            cancel, asyncio.wait_for(proc.communicate(input=payload), timeout=timeout_s)
        )
    except asyncio.TimeoutError:
        await terminate_process_tree(proc)
        return ProcessOutcome(exit_code=-1, timed_out=True)
    if cancelado:
        await terminate_process_tree(proc)
        return ProcessOutcome(exit_code=-1, cancelled=True)
    stdout, stderr = salida
    return ProcessOutcome(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or b"",
        stderr=stderr or b"",
    )
