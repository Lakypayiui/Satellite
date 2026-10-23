"""Fixed run/test command profiles for ``code_task``.

Profiles are the only way ``code_task`` executes anything: there is no field
for a command, arguments or environment. A profile is a list of argv steps and
an optional cleanup directory, never a shell string.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from satellite.code.languages import language_for_path, normalize_language


@dataclass(frozen=True)
class Step:
    argv: tuple[str, ...]
    display: str = ""

    def __post_init__(self) -> None:
        if not self.display:
            object.__setattr__(self, "display", shlex.join(self.argv))


@dataclass(frozen=True)
class Plan:
    status: str = "ok"
    steps: tuple[Step, ...] = ()
    reason: str = ""
    hint: str = ""
    cleanup: tuple[str, ...] = ()


def _missing(name: str, hint: str) -> Plan:
    return Plan(
        status="tool_missing",
        reason=f"no se encontró '{name}' en el PATH",
        hint=hint,
    )


def _unsupported(reason: str, hint: str = "") -> Plan:
    return Plan(status="unsupported", reason=reason, hint=hint)


def _invalid(reason: str) -> Plan:
    return Plan(status="invalid", reason=reason)


def _which(name: str) -> str | None:
    return shutil.which(name)


def _local_bin(root: Path, *names: str) -> str | None:
    base = root / "node_modules" / ".bin"
    suffixes = ("", ".cmd", ".bat", ".exe") if os.name == "nt" else ("",)
    for name in names:
        for suffix in suffixes:
            candidate = base / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return None


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _find_up(start: Path, root: Path, patterns: tuple[str, ...]) -> Path | None:
    """Nearest file matching one of ``patterns``, from ``start`` up to ``root``."""
    current = start
    try:
        current.relative_to(root)
    except ValueError:
        current = root
    while True:
        for pattern in patterns:
            try:
                matches = sorted(current.glob(pattern))
            except OSError:
                matches = []
            if matches:
                return matches[0]
        if current == root or current.parent == current:
            return None
        current = current.parent


def _project_python(root: Path, sandbox_n1: bool) -> str:
    if sandbox_n1:
        return "python"
    candidates = (
        root / ".venv" / "bin" / "python",
        root / "venv" / "bin" / "python",
        root / "env" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "Scripts" / "python.exe",
        root / "env" / "Scripts" / "python.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable or "python"


def _uses_pytest(root: Path) -> bool:
    for name in ("pytest.ini", "conftest.py"):
        if (root / name).is_file():
            return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        text = _read_text(root / name)
        if "pytest" in text.lower():
            return True
    for pattern in ("requirements*.txt", "dev-requirements*.txt"):
        for requirement in root.glob(pattern):
            if "pytest" in _read_text(requirement).lower():
                return True
    tests = root / "tests"
    if tests.is_dir():
        for index, file in enumerate(tests.rglob("*.py")):
            if index >= 200:
                break
            text = _read_text(file, limit=20_000)
            if "import pytest" in text or "from pytest" in text:
                return True
    return False


def _package_test_script(root: Path) -> bool:
    package = _read_json(root / "package.json")
    scripts = package.get("scripts")
    return isinstance(scripts, dict) and bool(str(scripts.get("test") or "").strip())


def _phpunit(root: Path) -> str | None:
    suffixes = ("", ".bat", ".cmd", ".exe") if os.name == "nt" else ("",)
    base = root / "vendor" / "bin" / "phpunit"
    for suffix in suffixes:
        candidate = Path(f"{base}{suffix}")
        if candidate.is_file():
            return str(candidate)
    return _which("phpunit")


def _composer_test(root: Path) -> bool:
    composer = _read_json(root / "composer.json")
    scripts = composer.get("scripts")
    return isinstance(scripts, dict) and "test" in scripts


def _cargo_manifest(start: Path, root: Path) -> Path | None:
    return _find_up(start, root, ("Cargo.toml",))


def _run_profile(
    language: str,
    *,
    root: Path,
    path: Path | None,
    filter_text: str | None,
    python_cmd: str | None,
    sandbox_n1: bool,
) -> Plan:
    if language in (
        "python",
        "javascript",
        "typescript",
        "java",
        "csharp",
        "go",
        "php",
        "cpp",
        "rust",
    ) and path is None:
        return _invalid("'path' es obligatorio para action='run' en este lenguaje")
    if language == "sql":
        return _unsupported(
            "SQL no se ejecuta: no hay conexión a base de datos",
            "usa 'lsp' diagnostics o 'code_task' action='test' con sqlfluff",
        )
    if path is not None and not path.is_file():
        return _invalid(f"no es un archivo: {path}")

    if language == "python":
        return Plan(
            steps=(Step((python_cmd or _project_python(root, sandbox_n1), str(path))),)
        )
    if language == "javascript":
        node = _which("node")
        if not node:
            return _missing("node", "instala Node.js 20+")
        return Plan(steps=(Step((node, str(path))),))
    if language == "typescript":
        tsx = _local_bin(root, "tsx")
        tsnode = _local_bin(root, "ts-node")
        npx = _which("npx")
        if tsx:
            return Plan(steps=(Step((tsx, str(path))),))
        if tsnode:
            return Plan(steps=(Step((tsnode, str(path))),))
        if npx:
            return Plan(steps=(Step((npx, "--no-install", "tsx", str(path))),))
        return _missing("tsx/ts-node", "npm install --save-dev tsx")
    if language == "java":
        java = _which("java")
        if not java:
            return _missing("java", "instala un JDK 17+ y pon 'java' en el PATH")
        return Plan(steps=(Step((java, str(path))),))
    if language == "csharp":
        dotnet = _which("dotnet")
        if not dotnet:
            return _missing("dotnet", "instala el SDK de .NET")
        project = _find_up(path.parent, root, ("*.csproj",))
        if project is not None:
            return Plan(steps=(Step((dotnet, "run", "--project", str(project))),))
        return Plan(steps=(Step((dotnet, "run")),))
    if language == "cpp":
        compiler = _which("c++") or _which("g++") or _which("clang++")
        if not compiler:
            return _missing("c++/g++/clang++", "instala un compilador de C++")
        output_dir = Path(tempfile.mkdtemp(prefix="satellite-code-"))
        binary = output_dir / ("program.exe" if os.name == "nt" else "program")
        return Plan(
            steps=(
                Step(
                    (
                        compiler,
                        "-std=c++20",
                        "-O0",
                        "-g",
                        str(path),
                        "-o",
                        str(binary),
                    )
                ),
                Step((str(binary),)),
            ),
            cleanup=(str(output_dir),),
        )
    if language == "go":
        go = _which("go")
        if not go:
            return _missing("go", "instala Go y pon 'go' en el PATH")
        return Plan(steps=(Step((go, "run", str(path))),))
    if language == "php":
        php = _which("php")
        if not php:
            return _missing("php", "instala PHP y pon 'php' en el PATH")
        return Plan(steps=(Step((php, str(path))),))
    if language == "rust":
        cargo = _which("cargo")
        manifest = _cargo_manifest(path.parent, root)
        if cargo and manifest is not None:
            return Plan(
                steps=(Step((cargo, "run", "--manifest-path", str(manifest))),)
            )
        rustc = _which("rustc")
        if not rustc:
            return _missing("cargo/rustc", "instala Rust con rustup")
        output_dir = Path(tempfile.mkdtemp(prefix="satellite-code-"))
        binary = output_dir / ("program.exe" if os.name == "nt" else "program")
        return Plan(
            steps=(
                Step((rustc, "-O", str(path), "-o", str(binary))),
                Step((str(binary),)),
            ),
            cleanup=(str(output_dir),),
        )
    return _unsupported(f"lenguaje no soportado: {language}")


def _python_test_plan(
    root: Path, python_cmd: str, filter_text: str | None, path: Path | None = None
) -> Plan:
    # Con ``path`` se prueba sólo ese archivo o directorio: sin él, cada verificación
    # de un agente lanzaba la suite entera.
    target = _relative_target(root, path)
    if _uses_pytest(root):
        argv = [python_cmd, "-m", "pytest", "-q"]
        if target:
            argv.append(target)
        if filter_text:
            argv.extend(["-k", filter_text])
        return Plan(steps=(Step(tuple(argv)),))
    if target and not (root / target).is_dir():
        argv = [python_cmd, "-m", "unittest", "-q", target]
    else:
        argv = [python_cmd, "-m", "unittest", "discover", "-q"]
        if target:
            argv.extend(["-s", target, "-t", "."])
    if filter_text:
        argv.extend(["-k", filter_text])
    return Plan(steps=(Step(tuple(argv)),))


def _relative_target(root: Path, path: Path | None) -> str | None:
    """``path`` relativa a ``root`` (None si no hay, es la raíz o queda fuera)."""
    if path is None:
        return None
    try:
        relative = Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return None
    text = relative.as_posix()
    return None if text in ("", ".") else text


def _test_profile(
    language: str,
    *,
    root: Path,
    path: Path | None,
    filter_text: str | None,
    python_cmd: str | None,
    sandbox_n1: bool,
) -> Plan:
    if language == "python":
        return _python_test_plan(
            root, python_cmd or _project_python(root, sandbox_n1), filter_text, path
        )
    if language in ("javascript", "typescript"):
        if not filter_text and _package_test_script(root):
            npm = _which("npm")
            if not npm:
                return _missing("npm", "instala Node.js 20+")
            return Plan(steps=(Step((npm, "test")),))
        vitest = _local_bin(root, "vitest")
        jest = _local_bin(root, "jest")
        if vitest:
            argv = [vitest, "run"]
            if filter_text:
                argv.extend(["-t", filter_text])
            return Plan(steps=(Step(tuple(argv)),))
        if jest:
            argv = [jest, "--ci"]
            if filter_text:
                argv.extend(["-t", filter_text])
            return Plan(steps=(Step(tuple(argv)),))
        if _package_test_script(root):
            npm = _which("npm")
            if not npm:
                return _missing("npm", "instala Node.js 20+")
            return Plan(steps=(Step((npm, "test")),))
        return _unsupported(
            "no hay script de test ni runner local (vitest/jest)",
            "añade scripts.test en package.json o instala vitest/jest",
        )
    if language == "java":
        wrapper = root / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if wrapper.is_file():
            argv = [str(wrapper), "test"]
            if filter_text:
                argv.extend(["--tests", filter_text])
            return Plan(steps=(Step(tuple(argv)),))
        if (root / "pom.xml").is_file():
            mvn = _which("mvn")
            if not mvn:
                return _missing("mvn", "instala Maven o añade un wrapper gradlew")
            argv = [mvn, "-q", "test"]
            if filter_text:
                argv.append(f"-Dtest={filter_text}")
            return Plan(steps=(Step(tuple(argv)),))
        if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
            gradle = _which("gradle")
            if not gradle:
                return _missing("gradle", "instala Gradle o añade un wrapper gradlew")
            argv = [gradle, "test"]
            if filter_text:
                argv.extend(["--tests", filter_text])
            return Plan(steps=(Step(tuple(argv)),))
        return _unsupported(
            "no se encontró pom.xml, build.gradle ni gradlew",
            "añade un build de Maven o Gradle",
        )
    if language == "csharp":
        dotnet = _which("dotnet")
        if not dotnet:
            return _missing("dotnet", "instala el SDK de .NET")
        argv = [dotnet, "test"]
        if filter_text:
            argv.extend(["--filter", filter_text])
        return Plan(steps=(Step(tuple(argv)),))
    if language == "cpp":
        if (root / "CMakeLists.txt").is_file():
            cmake = _which("cmake")
            ctest = _which("ctest")
            if not cmake or not ctest:
                return _missing("cmake/ctest", "instala CMake")
            build = root / "build"
            argv_ctest = [ctest, "--test-dir", str(build), "--output-on-failure"]
            if filter_text:
                argv_ctest.extend(["-R", filter_text])
            return Plan(
                steps=(
                    Step((cmake, "-S", str(root), "-B", str(build))),
                    Step((cmake, "--build", str(build))),
                    Step(tuple(argv_ctest)),
                )
            )
        if (root / "Makefile").is_file() or (root / "makefile").is_file():
            make = _which("make")
            if not make:
                return _missing("make", "instala make")
            return Plan(steps=(Step((make, "test")),))
        return _unsupported(
            "no se encontró CMakeLists.txt ni Makefile con target test",
            "añade CMake+CTest o un target 'test'",
        )
    if language == "go":
        go = _which("go")
        if not go:
            return _missing("go", "instala Go")
        argv = [go, "test", "./..."]
        if filter_text:
            argv.extend(["-run", filter_text])
        return Plan(steps=(Step(tuple(argv)),))
    if language == "php":
        composer = _which("composer")
        if composer and _composer_test(root):
            return Plan(steps=(Step((composer, "test")),))
        phpunit = _phpunit(root)
        if not phpunit:
            return _unsupported(
                "no hay script 'test' en composer.json ni PHPUnit",
                "añade PHPUnit o 'composer test'",
            )
        argv = [phpunit]
        if filter_text:
            argv.extend(["--filter", filter_text])
        return Plan(steps=(Step(tuple(argv)),))
    if language == "rust":
        cargo = _which("cargo")
        if not cargo:
            return _missing("cargo", "instala Rust con rustup")
        argv = [cargo, "test"]
        if filter_text:
            argv.append(filter_text)
        return Plan(steps=(Step(tuple(argv)),))
    if language == "sql":
        sqlfluff = _which("sqlfluff")
        if not sqlfluff:
            return _unsupported(
                "no hay sqlfluff instalado; SQL no se ejecuta contra una base de datos",
                "pip install sqlfluff",
            )
        target = str(path) if path is not None else str(root)
        return Plan(steps=(Step((sqlfluff, "lint", target)),))
    return _unsupported(f"lenguaje no soportado: {language}")


def build_plan(
    language: str | None,
    action: str,
    *,
    root: Path,
    path: Path | None = None,
    filter_text: str | None = None,
    python_cmd: str | None = None,
    sandbox_n1: bool = False,
) -> Plan:
    """Build a fixed profile for ``action`` (``run`` or ``test``)."""
    root = Path(root)
    resolved = normalize_language(language)
    if resolved is None and path is not None:
        resolved = language_for_path(path)
    if resolved is None:
        return _invalid("no se pudo determinar el lenguaje; usa 'language'")
    if action not in ("run", "test"):
        return _invalid("'action' debe ser 'run' o 'test'")
    if action == "run":
        return _run_profile(
            resolved,
            root=root,
            path=path,
            filter_text=filter_text,
            python_cmd=python_cmd,
            sandbox_n1=sandbox_n1,
        )
    return _test_profile(
        resolved,
        root=root,
        path=path,
        filter_text=filter_text,
        python_cmd=python_cmd,
        sandbox_n1=sandbox_n1,
    )
