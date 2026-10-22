"""Auditoría estática de skills y lo que implica para la memoización."""

from pathlib import Path

from satellite.sandbox.base import SandboxLevel, SandboxSpec
from satellite.sandbox.docker_sandbox import DockerSandbox
from satellite.skills.audit import audit_skill, code_digest, reason_for
from satellite.skills.cache import SkillResultCache
from satellite.skills.models import SkillManifest
from satellite.skills.registry import SkillRegistry


def write_skill(
    root: Path, script: str, *, skill_id: str = "s1", modules: dict | None = None
) -> Path:
    """Crea ``<root>/<id>/main.py`` (y módulos locales) y devuelve el directorio."""
    directory = root / skill_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "main.py").write_text(script, encoding="utf-8")
    for name, text in (modules or {}).items():
        (directory / f"{name}.py").write_text(text, encoding="utf-8")
    return directory


def register_skill(root: Path, script: str, *, skill_id: str = "s1", **extra) -> SkillRegistry:
    """Registra una skill de verdad en disco (con su script tal cual)."""
    registry = SkillRegistry(root)
    manifest = SkillManifest(
        id=skill_id,
        description="skill de prueba",
        deterministic=extra.pop("deterministic", True),
        **extra,
    )
    registry.register(manifest, script)
    return registry


# ---------------------------------------------------------------- auditoría


def test_clean_script_has_no_findings(tmp_path: Path) -> None:
    directory = write_skill(
        tmp_path, "import json\nprint(json.dumps({'ok': True}))\n"
    )
    assert audit_skill(directory) == []
    assert reason_for([]) == ""


def test_alias_and_from_imports_are_detected(tmp_path: Path) -> None:
    """Se compara el módulo real, no el alias."""
    for script in (
        "import random as r\nprint(r.random())\n",
        "from random import randint\nprint(randint(1, 2))\n",
        "from datetime import datetime\nprint(datetime.now())\n",
        "import urllib.parse\nprint(urllib.parse.quote('x'))\n",
    ):
        findings = audit_skill(write_skill(tmp_path, script))
        assert findings, script
        assert findings[0].kind == "import"


def test_dynamic_execution_is_detected(tmp_path: Path) -> None:
    for script in (
        "print(eval('1'))\n",
        "exec('x = 1')\n",
        "print(__import__('os'))\n",
        "import importlib\n",
        "import os\nos.system('ls')\n",
        "import subprocess\n",
    ):
        assert audit_skill(write_skill(tmp_path, script)), script


def test_effects_are_detected(tmp_path: Path) -> None:
    for script in (
        "import os\nprint(os.environ)\n",
        "import os\nprint(os.getenv('X'))\n",
        "open('f', 'w').write('x')\n",
        "with open('f', mode='a') as fh:\n    fh.write('x')\n",
        "from pathlib import Path\nPath('f').write_text('x')\n",
        "import pathlib\npathlib.Path('f').write_bytes(b'x')\n",
    ):
        findings = audit_skill(write_skill(tmp_path, script))
        assert findings, script
        assert any(finding.kind in ("effect", "import") for finding in findings)


def test_local_module_is_audited_recursively(tmp_path: Path) -> None:
    clean = write_skill(
        tmp_path,
        "from helper import do\nprint(do())\n",
        modules={"helper": "def do():\n    return 1\n"},
    )
    assert audit_skill(clean) == []

    dirty = write_skill(
        tmp_path,
        "import helper\nprint(helper.do())\n",
        modules={"helper": "import time\n\ndef do():\n    return time.time()\n"},
    )
    findings = audit_skill(dirty)
    assert findings
    assert any("helper" in finding.file for finding in findings)


def test_unresolvable_dependency_is_a_finding(tmp_path: Path) -> None:
    """Un import que no es stdlib ni local no se puede auditar: fail-closed."""
    findings = audit_skill(write_skill(tmp_path, "import pandas\nprint(pandas)\n"))
    assert findings
    assert findings[0].kind == "dependency"
    assert "pandas" in findings[0].detail


def test_syntax_error_missing_file_and_depth_are_fail_closed(tmp_path: Path) -> None:
    assert audit_skill(write_skill(tmp_path, "def (\n"))  # sintaxis inválida
    assert audit_skill(tmp_path / "no-existe")  # no se pudo leer
    nested = write_skill(
        tmp_path,
        "import helper\nprint(helper.x)\n",
        modules={"helper": "import deeper\nx = deeper.y\n", "deeper": "y = 1\n"},
    )
    assert audit_skill(nested, max_depth=0)  # profundidad agotada


def test_bash_is_not_auditable(tmp_path: Path) -> None:
    directory = write_skill(tmp_path, "echo hola\n")
    findings = audit_skill(directory, language="bash")
    assert findings and findings[0].kind == "language"


# ---------------------------------------------------------------- caché


def test_deterministic_but_dirty_skill_is_not_memoized(tmp_path: Path) -> None:
    """Declararse determinista ya no basta: el código tiene que acompañar."""
    root = tmp_path / "skills"
    registry = register_skill(root, "import random\nprint(random.random())\n")
    manifest = registry.get("s1")
    cache = SkillResultCache()

    assert cache.eligible(manifest, skill_dir=root / "s1") is False
    reason = cache.reason(manifest, skill_dir=root / "s1")
    assert "auditoría" in reason and "random" in reason


def test_clean_deterministic_skill_is_memoizable(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    registry = register_skill(root, "import json\nprint(json.dumps({'n': 1}))\n")
    manifest = registry.get("s1")
    cache = SkillResultCache()
    assert cache.eligible(manifest, skill_dir=root / "s1") is True
    assert cache.reason(manifest, skill_dir=root / "s1") == ""


def test_bash_skill_is_never_memoized(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    registry = register_skill(
        root, "echo '{\"ok\": true}'\n", language="bash"
    )
    manifest = registry.get("s1")
    cache = SkillResultCache()
    assert cache.eligible(manifest, skill_dir=root / "s1") is False
    assert "no auditable" in cache.reason(manifest, skill_dir=root / "s1")


def test_audit_is_remembered_per_skill(tmp_path: Path) -> None:
    """La auditoría se cachea por la huella real del código: no se repite."""
    root = tmp_path / "skills"
    registry = register_skill(root, "import random\n")
    manifest = registry.get("s1")
    cache = SkillResultCache()
    first = cache.audit(manifest, root / "s1")
    assert first
    assert cache.audit(manifest, root / "s1") is first


def test_edited_entrypoint_without_manifest_update_is_stale(tmp_path: Path) -> None:
    """Editar el script deja el manifest obsoleto: no se reutiliza la auditoría."""
    root = tmp_path / "skills"
    registry = register_skill(root, "print(1)\n")
    manifest = registry.get("s1")
    skill_dir = root / "s1"
    cache = SkillResultCache()

    assert cache.eligible(manifest, skill_dir=skill_dir) is True
    first = cache.audit(manifest, skill_dir)
    assert first == []

    (skill_dir / "main.py").write_text("print(2)\n", encoding="utf-8")
    findings = cache.audit(manifest, skill_dir)

    assert findings is not first
    assert any(finding.kind == "stale_manifest" for finding in findings)
    assert cache.eligible(manifest, skill_dir=skill_dir) is False
    assert "hash del manifiesto" in cache.reason(manifest, skill_dir=skill_dir)

    # Al actualizar el manifest, la misma versión vuelve a ser memoizable.
    fixed = SkillManifest.from_dict(
        {
            **manifest.to_dict(),
            "hash": code_digest(skill_dir, manifest.entrypoint),
        }
    )
    assert cache.eligible(fixed, skill_dir=skill_dir) is True


def test_edited_local_module_without_manifest_update_is_stale(tmp_path: Path) -> None:
    """Los módulos locales también forman parte de la huella del código."""
    root = tmp_path / "skills"
    script = "from helper import valor\nprint(valor)\n"
    write_skill(root, script, modules={"helper": "valor = 1\n"})
    registry = SkillRegistry(root)
    manifest = registry.register(
        SkillManifest(id="s1", description="skill de prueba", deterministic=True),
        script,
    )
    skill_dir = root / "s1"
    cache = SkillResultCache()

    assert manifest.hash == code_digest(skill_dir, manifest.entrypoint)
    assert cache.eligible(manifest, skill_dir=skill_dir) is True
    before = cache.key_for(manifest, {}, project_root=tmp_path, skill_dir=skill_dir)

    (skill_dir / "helper.py").write_text("valor = 2\n", encoding="utf-8")
    findings = cache.audit(manifest, skill_dir)

    assert any(finding.kind == "stale_manifest" for finding in findings)
    assert cache.eligible(manifest, skill_dir=skill_dir) is False
    assert (
        cache.key_for(manifest, {}, project_root=tmp_path, skill_dir=skill_dir)
        != before
    )


# ---------------------------------------------------------------- persistencia


def test_cache_with_path_persists_on_every_put(tmp_path: Path) -> None:
    path = tmp_path / "state" / "skill_cache.json"
    cache = SkillResultCache(path)
    cache.put("k", {"ok": 1}, skill_id="s1")

    assert path.exists()
    fresh = SkillResultCache(path)
    assert fresh.get("k") == {"ok": 1}
    assert fresh.keys_for("s1") == {"k"}
    # escritura atómica: no queda ningún temporal por ahí
    assert list(path.parent.glob("*.tmp")) == []


def test_tolerates_a_corrupt_cache_file(tmp_path: Path) -> None:
    path = tmp_path / "skill_cache.json"
    path.write_text("{no soy json", encoding="utf-8")
    cache = SkillResultCache(path)  # no lanza
    assert len(cache) == 0
    cache.put("k", {"ok": 1}, skill_id="s1")
    assert len(cache) == 1


def test_entry_over_the_byte_cap_is_skipped(tmp_path: Path) -> None:
    cache = SkillResultCache(tmp_path / "c.json", max_entry_bytes=64)
    cache.put("k", {"grande": "x" * 500}, skill_id="s1")
    assert len(cache) == 0
    assert cache.stats().skipped == 1
    # un resultado pequeño sí entra
    cache.put("k2", {"ok": 1}, skill_id="s1")
    assert len(cache) == 1


def test_eviction_keeps_the_cache_bounded() -> None:
    cache = SkillResultCache(max_entries=2)
    for index in range(4):
        cache.put(f"k{index}", {"i": index}, skill_id="s1")
    assert len(cache) == 2
    assert cache.keys_for("s1") == {"k2", "k3"}
    # la desalojada ya no se sirve
    assert cache.get("k0") is None


# ---------------------------------------------------------------- aislamiento


def test_n1_mounts_the_workspace_read_only(tmp_path: Path) -> None:
    """En N1 el workspace va ``:ro`` y el estado no se monta aparte."""
    state = tmp_path / ".satellite" / "state"
    state.mkdir(parents=True, exist_ok=True)
    spec = SandboxSpec(project_root=tmp_path, level=SandboxLevel.N1, workdir=tmp_path)
    argv = DockerSandbox().build_argv(["python", "main.py"], spec)

    mounts = [argv[index + 1] for index, part in enumerate(argv) if part == "-v"]
    assert mounts
    assert all(mount.endswith(":ro") for mount in mounts)
    assert not any(str(state) in mount for mount in mounts)


# ---------------------------------------------------------------- CLI


def test_cli_skills_list_explains_why_it_is_not_memoized(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from satellite.cli.app import app

    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    register_skill(
        home / "skills", "import random\nprint(random.random())\n", skill_id="dirty"
    )

    result = CliRunner().invoke(app, ["skills", "list"])

    assert result.exit_code == 0, result.output
    assert "NO memoizable" in result.output
    assert "auditoría" in result.output
    assert "random" in result.output


def test_cli_skills_list_marks_an_audited_deterministic_skill(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from satellite.cli.app import app

    home = tmp_path / "home"
    monkeypatch.setenv("SATELLITE_HOME", str(home))
    register_skill(
        home / "skills", "import json\nprint(json.dumps({'ok': True}))\n", skill_id="clean"
    )

    result = CliRunner().invoke(app, ["skills", "list"])

    assert result.exit_code == 0, result.output
    assert "memoizable: determinista (auditado)" in result.output
