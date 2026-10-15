"""Memoización de skills deterministas (F6)."""

import asyncio
import json
from pathlib import Path

from satellite.sandbox.base import SandboxLevel, SandboxResult, SandboxRunner, SandboxSpec
from satellite.skills.cache import SkillResultCache, content_digest, unsafe_permissions
from satellite.skills.models import SkillManifest, SkillStatus
from satellite.skills.registry import SkillRegistry
from satellite.skills.runner import SkillRunner


def run(coro):
    return asyncio.run(coro)


class FakeSandbox(SandboxRunner):
    """Sandbox de mentira: cuenta ejecuciones y devuelve una salida fija."""

    level = SandboxLevel.N0

    def __init__(self, stdout: str = '{"ok": true}', exit_code: int = 0, error=None) -> None:
        self.calls = 0
        self.stdout = stdout
        self.exit_code = exit_code
        self.error = error

    async def run(self, argv: list[str], spec: SandboxSpec) -> SandboxResult:
        self.calls += 1
        return SandboxResult(
            exit_code=self.exit_code, stdout=self.stdout, stderr="", error=self.error
        )


def make_manifest(
    *, skill_id: str = "count_words", deterministic: bool = True, permissions: dict | None = None
) -> SkillManifest:
    return SkillManifest(
        id=skill_id,
        description="cuenta palabras de un texto",
        permissions=dict(permissions or {}),
        deterministic=deterministic,
    )


def make_registry(tmp_path: Path, **kwargs) -> SkillRegistry:
    registry = SkillRegistry(tmp_path)
    registry.register(make_manifest(**kwargs), "print(1)")
    return registry


def make_runner(tmp_path: Path, *, cache=None, sandbox=None, **kwargs):
    registry = make_registry(tmp_path, **kwargs)
    sandbox = sandbox or FakeSandbox()
    return SkillRunner(registry, sandbox, cache=cache), registry, sandbox


# ---------------------------------------------------------------- política


def test_only_deterministic_skills_are_memoizable() -> None:
    cache = SkillResultCache()
    assert cache.eligible(make_manifest(deterministic=True)) is True
    assert cache.eligible(make_manifest(deterministic=False)) is False
    assert "deterministic" in cache.reason(make_manifest(deterministic=False))


def test_unsafe_permissions_block_memoization() -> None:
    cache = SkillResultCache()
    manifest = make_manifest(permissions={"network": True})
    assert cache.eligible(manifest) is False
    assert "network" in cache.reason(manifest)
    # declararlo en falso es seguro
    assert cache.eligible(make_manifest(permissions={"network": False})) is True


def test_unsafe_permissions_detection() -> None:
    assert unsafe_permissions({"network": False}) == []
    assert unsafe_permissions({"network": True}) == ["network"]
    assert unsafe_permissions({"write_files": 1}) == ["write_files"]
    assert unsafe_permissions({"random": True, "clock": True}) == ["clock", "random"]
    assert unsafe_permissions({"notas": "da igual"}) == []
    assert unsafe_permissions(None) == []


def test_quarantined_and_networked_skills_are_not_memoizable() -> None:
    cache = SkillResultCache()
    quarantined = make_manifest().with_status(SkillStatus.QUARANTINED)
    assert cache.eligible(quarantined) is False
    assert "quarantined" in cache.reason(quarantined)
    # si hay que abrir red para ejecutarla, deja de ser determinista
    assert cache.eligible(make_manifest(), allow_network=True) is False


# ---------------------------------------------------------------- clave


def test_key_is_stable_and_depends_on_manifest_script_and_input(tmp_path: Path) -> None:
    cache = SkillResultCache()
    registry = make_registry(tmp_path)
    manifest = registry.get("count_words")

    first = cache.key_for(manifest, {"texto": "hola"}, project_root=tmp_path)
    assert first == cache.key_for(manifest, {"texto": "hola"}, project_root=tmp_path)
    assert first != cache.key_for(manifest, {"texto": "otro"}, project_root=tmp_path)

    # cambia el script (hash) -> clave nueva
    re_registered = registry.register(
        SkillManifest(id="count_words", description="cuenta palabras de un texto", deterministic=True),
        "print(2)",
    )
    assert cache.key_for(re_registered, {"texto": "hola"}, project_root=tmp_path) != first

    # cambia la versión -> clave nueva
    bumped = SkillManifest.from_dict({**manifest.to_dict(), "version": "2.0.0"})
    assert cache.key_for(bumped, {"texto": "hola"}, project_root=tmp_path) != first


def test_key_includes_content_of_referenced_files(tmp_path: Path) -> None:
    cache = SkillResultCache()
    registry = make_registry(tmp_path)
    manifest = registry.get("count_words")
    doc = tmp_path / "doc.txt"
    doc.write_text("uno", encoding="utf-8")

    before = cache.key_for(manifest, {"path": "doc.txt"}, project_root=tmp_path)
    doc.write_text("dos", encoding="utf-8")
    after = cache.key_for(manifest, {"path": "doc.txt"}, project_root=tmp_path)
    assert before != after
    # un fichero que no existe no aporta contenido
    assert content_digest({"path": "no-existe.txt"}, tmp_path) == ""
    assert content_digest({"path": "doc.txt"}, None) == ""


def test_content_digest_walks_nested_payloads(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    digest = content_digest({"files": ["a.txt"], "meta": {"extra": "a.txt"}}, tmp_path)
    assert digest != ""
    assert digest == content_digest({"meta": {"extra": "a.txt"}, "files": ["a.txt"]}, tmp_path)


# ---------------------------------------------------------------- almacén


def test_store_put_get_invalidate_and_stats() -> None:
    cache = SkillResultCache()
    assert cache.get("k1") is None  # miss
    cache.put("k1", {"ok": 1}, skill_id="uno")
    cache.put("k2", {"ok": 2}, skill_id="dos")
    assert cache.get("k1") == {"ok": 1}  # hit
    assert cache.stats().hits == 1 and cache.stats().misses == 1
    assert cache.stats().hit_rate == 0.5
    assert len(cache) == 2

    assert cache.keys_for("uno") == {"k1"}
    assert cache.invalidate("uno") == 1
    assert cache.get("k1") is None  # ya no está (miss)
    assert cache.get("k2") == {"ok": 2}  # la otra skill sigue
    assert cache.invalidate("uno") == 0

    cache.clear()
    assert len(cache) == 0 and cache.stats().hits == 0


def test_persistence_roundtrip_and_tolerance(tmp_path: Path) -> None:
    path = tmp_path / "skill_cache.json"
    cache = SkillResultCache()
    cache.put("k", {"ok": 1}, skill_id="uno")
    cache.save(path)

    restored = SkillResultCache()
    restored.load(path)
    assert restored.get("k") == {"ok": 1}
    assert restored.keys_for("uno") == {"k"}

    broken = tmp_path / "roto.json"
    broken.write_text("{no soy json", encoding="utf-8")
    tolerant = SkillResultCache()
    tolerant.load(broken)  # no lanza
    assert len(tolerant) == 0


# ---------------------------------------------------------------- runner


def test_deterministic_skill_is_memoized(tmp_path: Path) -> None:
    cache = SkillResultCache()
    runner, registry, sandbox = make_runner(tmp_path, cache=cache)

    first = run(runner.run("count_words", {"texto": "hola"}, project_root=tmp_path))
    assert first.success and first.cached is False
    second = run(runner.run("count_words", {"texto": "hola"}, project_root=tmp_path))
    assert second.success and second.cached is True
    assert second.output == first.output
    assert sandbox.calls == 1  # no se volvió a ejecutar
    # un acierto cuenta como uso con coste nulo
    assert registry.get("count_words").stats.uses == 2
    assert registry.get("count_words").stats.total_latency_ms == first.duration_ms


def test_different_input_runs_again(tmp_path: Path) -> None:
    cache = SkillResultCache()
    runner, _registry, sandbox = make_runner(tmp_path, cache=cache)
    run(runner.run("count_words", {"texto": "uno"}, project_root=tmp_path))
    run(runner.run("count_words", {"texto": "dos"}, project_root=tmp_path))
    assert sandbox.calls == 2


def test_failures_are_never_memoized(tmp_path: Path) -> None:
    cache = SkillResultCache()
    runner, _registry, sandbox = make_runner(
        tmp_path, cache=cache, sandbox=FakeSandbox(exit_code=1, stdout="")
    )
    first = run(runner.run("count_words", {"texto": "x"}, project_root=tmp_path))
    second = run(runner.run("count_words", {"texto": "x"}, project_root=tmp_path))
    assert first.success is False and second.success is False
    assert sandbox.calls == 2
    assert len(cache) == 0


def test_non_deterministic_skill_is_not_memoized(tmp_path: Path) -> None:
    cache = SkillResultCache()
    runner, _registry, sandbox = make_runner(tmp_path, cache=cache, deterministic=False)
    run(runner.run("count_words", {"texto": "x"}, project_root=tmp_path))
    run(runner.run("count_words", {"texto": "x"}, project_root=tmp_path))
    assert sandbox.calls == 2
    assert len(cache) == 0


def test_skill_with_unsafe_permissions_is_not_memoized(tmp_path: Path) -> None:
    cache = SkillResultCache()
    runner, _registry, sandbox = make_runner(
        tmp_path, cache=cache, permissions={"network": True}
    )
    run(runner.run("count_words", {"texto": "x"}, project_root=tmp_path))
    run(runner.run("count_words", {"texto": "x"}, project_root=tmp_path))
    assert sandbox.calls == 2


def test_referenced_file_change_invalidates_the_result(tmp_path: Path) -> None:
    cache = SkillResultCache()
    runner, _registry, sandbox = make_runner(
        tmp_path, cache=cache, sandbox=FakeSandbox(stdout='{"palabras": 1}')
    )
    doc = tmp_path / "doc.txt"
    doc.write_text("uno", encoding="utf-8")
    run(runner.run("count_words", {"path": "doc.txt"}, project_root=tmp_path))
    doc.write_text("uno dos", encoding="utf-8")
    run(runner.run("count_words", {"path": "doc.txt"}, project_root=tmp_path))
    assert sandbox.calls == 2


def test_runner_without_cache_always_executes(tmp_path: Path) -> None:
    runner, _registry, sandbox = make_runner(tmp_path)
    run(runner.run("count_words", {"texto": "x"}, project_root=tmp_path))
    run(runner.run("count_words", {"texto": "x"}, project_root=tmp_path))
    assert sandbox.calls == 2


def test_cache_roundtrip_through_disk_avoids_execution(tmp_path: Path) -> None:
    """La cache guardada en disco sirve el resultado en otro proceso/proceso nuevo."""
    path = tmp_path / "skill_cache.json"
    cache = SkillResultCache()
    runner, _registry, sandbox = make_runner(
        tmp_path, cache=cache, sandbox=FakeSandbox(stdout='{"palabras": 2}')
    )
    run(runner.run("count_words", {"path": "doc.txt", "texto": "x"}, project_root=tmp_path))
    cache.save(path)

    fresh = SkillResultCache()
    fresh.load(path)
    runner2, _registry2, sandbox2 = make_runner(tmp_path, cache=fresh)
    result = run(runner2.run("count_words", {"path": "doc.txt", "texto": "x"}, project_root=tmp_path))
    assert result.cached is True
    assert sandbox2.calls == 0
    assert json.loads(json.dumps(result.output)) == {"palabras": 2}
