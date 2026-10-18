"""API de escritorio: comentarios inline, cola persistida, git y worktrees."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from satellite.desktop.app import create_app  # noqa: E402
from satellite.desktop.backend import DesktopBackend  # noqa: E402

TOKEN = "token-workspace"
SIN_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git no está instalado")


class FakeSession:
    """Sesión mínima: las rutas de git/cola/comentarios no necesitan runtime de verdad.

    Evita que ``set_worktree`` (que rearma el runtime) toque el HOME real del usuario.
    """

    def __init__(self, root, home=None) -> None:
        self.root = Path(root)
        self.home = home

    def runtime(self, *, force: bool = False):
        return (None, None, None, None)


def make_client(project: Path) -> TestClient:
    # Recents y home viven FUERA del proyecto: dentro de un repo los harían "sucios".
    soporte = project.parent / f"{project.name}-satellite-test"
    soporte.mkdir(parents=True, exist_ok=True)
    backend = DesktopBackend(
        project,
        token=TOKEN,
        home=soporte / "home",
        recents_path=soporte / "recents.json",
        session_factory=FakeSession,
    )
    client = TestClient(create_app(backend))
    client.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return client


# ---------------------------------------------------------- comentarios inline


def test_comments_are_persisted_and_can_be_resolved(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    creado = client.post(
        "/api/comments",
        json={
            "file": "src/app.py",
            "line_start": 3,
            "line_end": 5,
            "text": "esto hay que revisarlo",
            "task_id": "t1",
        },
    )
    assert creado.status_code == 200
    comentario = creado.json()
    assert comentario["file"] == "src/app.py"
    assert comentario["line_start"] == 3 and comentario["line_end"] == 5
    assert comentario["resolved"] is False

    listado = client.get("/api/comments", params={"task_id": "t1"}).json()["comments"]
    assert [c["id"] for c in listado] == [comentario["id"]]

    resuelto = client.patch(
        f"/api/comments/{comentario['id']}", json={"resolved": True}
    )
    assert resuelto.status_code == 200
    assert resuelto.json()["resolved"] is True

    pendientes = client.get(
        "/api/comments", params={"task_id": "t1", "include_resolved": False}
    ).json()["comments"]
    assert pendientes == []

    # Persiste entre "recargas": otro cliente sobre el mismo proyecto lo ve.
    otro = make_client(tmp_path)
    assert len(otro.get("/api/comments").json()["comments"]) == 1

    assert client.delete(f"/api/comments/{comentario['id']}").status_code == 200
    assert client.delete("/api/comments/no-existe").status_code == 404


def test_comment_validation(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    vacio = client.post("/api/comments", json={"file": "a", "line_start": 1, "text": ""})
    linea_cero = client.post("/api/comments", json={"file": "a", "line_start": 0, "text": "x"})
    escape = client.post(
        "/api/comments", json={"file": "../fuera", "line_start": 1, "text": "x"}
    )

    assert vacio.status_code == 422
    assert linea_cero.status_code == 422
    assert escape.status_code == 422
    assert escape.json()["error"]["code"] == "invalid_comment"


# ------------------------------------------------------------ cola de prompts


def test_queue_is_persisted_ordered_and_editable(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    assert client.post("/api/queue", json={"text": "primero"}).status_code == 200
    assert client.post("/api/queue", json={"text": "segundo"}).status_code == 200

    items = client.get("/api/queue").json()["items"]
    assert [item["text"] for item in items] == ["primero", "segundo"]

    editado = client.patch(
        f"/api/queue/{items[0]['id']}", json={"text": "primero (corregido)"}
    )
    assert editado.status_code == 200
    assert editado.json()["items"][0]["text"] == "primero (corregido)"

    quitado = client.delete(f"/api/queue/{items[1]['id']}")
    assert [item["text"] for item in quitado.json()["items"]] == ["primero (corregido)"]

    # Persiste entre recargas.
    otro = make_client(tmp_path)
    assert [item["text"] for item in otro.get("/api/queue").json()["items"]] == [
        "primero (corregido)"
    ]

    assert client.post("/api/queue/clear").json()["items"] == []

    invalido = client.post("/api/queue", json={"text": "   "})
    assert invalido.status_code == 422


# ------------------------------------------------------------------- git


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("hola\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "inicial")
    return root


@SIN_GIT
def test_git_status_stage_and_commit_over_the_api(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    client = make_client(root)

    estado = client.get("/api/git/status").json()
    assert estado["is_repo"] is True and estado["clean"] is True

    (root / "a.txt").write_text("v2\n", encoding="utf-8")
    preparado = client.post("/api/git/stage", json={"paths": ["a.txt"]})
    assert preparado.status_code == 200
    assert any(f["path"] == "a.txt" for f in preparado.json()["status"]["staged"])

    confirmado = client.post("/api/git/commit", json={"message": "cambio"})
    assert confirmado.status_code == 200
    assert confirmado.json()["status"]["clean"] is True

    fuera = client.post("/api/git/stage", json={"paths": ["../x"]})
    assert fuera.status_code == 409
    assert fuera.json()["error"]["code"] == "git_error"


@SIN_GIT
def test_worktrees_over_the_api(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    client = make_client(root)

    creado = client.post("/api/worktrees", json={"name": "feature"})
    assert creado.status_code == 200
    vista = creado.json()
    assert vista["is_repo"] is True
    assert any(w["branch"] == "wt/feature" for w in vista["worktrees"])

    destino = vista["created"]["path"]
    activado = client.post("/api/worktrees/active", json={"path": destino})
    assert activado.status_code == 200
    assert activado.json()["active"] == str(Path(destino).resolve())

    vuelto = client.post("/api/worktrees/active", json={"path": ""})
    assert vuelto.json()["active"] == ""

    quitado = client.post("/api/worktrees/remove", json={"path": destino})
    assert quitado.status_code == 200
    assert not Path(destino).exists()
