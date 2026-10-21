"""Concise summaries for common test-runner and compiler output."""

from __future__ import annotations

import re

_TAIL_CHARS = 40_000


def _tail(stdout: str, stderr: str) -> str:
    return ((stdout or "") + "\n" + (stderr or ""))[-_TAIL_CHARS:]


def _count(pattern: str, text: str, group: int = 1) -> int | None:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    try:
        return int(match.group(group))
    except (TypeError, ValueError):
        return None


def _join(parts: list[str]) -> str:
    return ", ".join(part for part in parts if part)


def _pytest(text: str) -> str | None:
    passed = _count(r"(\d+) passed", text)
    failed = _count(r"(\d+) failed", text)
    errors = _count(r"(\d+) error", text)
    skipped = _count(r"(\d+) skipped", text)
    if passed is None and failed is None and errors is None:
        return None
    return _join(
        [
            f"{passed} passed" if passed is not None else "",
            f"{failed} failed" if failed else "",
            f"{errors} errors" if errors else "",
            f"{skipped} skipped" if skipped else "",
        ]
    )


def _unittest(text: str) -> str | None:
    match = re.search(r"Ran (\d+) tests?", text, re.IGNORECASE)
    if not match:
        return None
    total = match.group(1)
    if re.search(r"^OK\b", text, re.MULTILINE):
        return f"{total} tests passed"
    if re.search(r"^FAILED\b", text, re.MULTILINE):
        failures = _count(r"failures=(\d+)", text) or 0
        errors = _count(r"errors=(\d+)", text) or 0
        return _join(
            [
                f"{total} tests",
                f"{failures} failures" if failures else "",
                f"{errors} errors" if errors else "",
            ]
        )
    return f"{total} tests"


def _jest_like(text: str) -> str | None:
    match = re.search(
        r"Tests:\s+(?:(\d+) failed,\s*)?(\d+) passed(?:,\s*(\d+) skipped)?",
        text,
    )
    if not match:
        match = re.search(r"Tests\s+(\d+) passed", text)
        if match:
            return f"{match.group(1)} passed"
        return None
    failed = match.group(1)
    passed = match.group(2)
    skipped = match.group(3)
    return _join(
        [
            f"{passed} passed" if passed else "",
            f"{failed} failed" if failed else "",
            f"{skipped} skipped" if skipped else "",
        ]
    )


def _go(text: str) -> str | None:
    failed_packages = len(re.findall(r"^FAIL\s+\S+", text, re.MULTILINE))
    failed_tests = len(re.findall(r"^--- FAIL:", text, re.MULTILINE))
    ok_packages = len(re.findall(r"^ok\s+\S+", text, re.MULTILINE))
    if not (failed_packages or failed_tests or ok_packages):
        return None
    return _join(
        [
            f"{ok_packages} packages ok" if ok_packages else "",
            f"{failed_packages} packages failed" if failed_packages else "",
            f"{failed_tests} tests failed" if failed_tests else "",
        ]
    )


def _cargo(text: str) -> str | None:
    match = re.search(
        r"test result: (ok|FAILED)\.\s+(\d+) passed;\s+(\d+) failed",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return _join(
        [
            f"{match.group(2)} passed",
            f"{match.group(3)} failed" if match.group(3) != "0" else "",
        ]
    ) or ("ok" if match.group(1).lower() == "ok" else "failed")


def _dotnet(text: str) -> str | None:
    match = re.search(
        r"Failed:\s*(\d+),\s*Passed:\s*(\d+)(?:,\s*Skipped:\s*(\d+))?",
        text,
        re.IGNORECASE,
    )
    if match:
        return _join(
            [
                f"{match.group(2)} passed",
                f"{match.group(1)} failed" if match.group(1) != "0" else "",
                f"{match.group(3)} skipped" if match.group(3) else "",
            ]
        )
    if "Passed!" in text:
        return "all tests passed"
    if "Failed!" in text:
        return "tests failed"
    return None


def _maven_gradle(text: str) -> str | None:
    if "BUILD SUCCESS" in text.upper():
        return "build successful"
    if "BUILD FAILED" in text.upper() or "BUILD FAILURE" in text.upper():
        return "build failed"
    return None


def _phpunit(text: str) -> str | None:
    match = re.search(r"OK \((\d+) tests?", text)
    if match:
        return f"{match.group(1)} passed"
    match = re.search(
        r"Tests:\s*(\d+),\s*Assertions:\s*\d+(?:,\s*Failures:\s*(\d+))?",
        text,
    )
    if match:
        return _join(
            [
                f"{match.group(1)} tests",
                f"{match.group(2)} failures" if match.group(2) else "",
            ]
        )
    if "FAILURES!" in text:
        return "tests failed"
    return None


def _ctest(text: str) -> str | None:
    match = re.search(
        r"(\d+)% tests passed,\s*(\d+) tests failed out of (\d+)", text
    )
    if match:
        return (
            f"{match.group(3)} tests, {match.group(2)} failed"
            if match.group(2) != "0"
            else f"{match.group(3)} tests passed"
        )
    return None


def _sqlfluff(text: str) -> str | None:
    match = re.search(r"Found (\d+) violation", text, re.IGNORECASE)
    if match:
        return f"{match.group(1)} violations"
    if re.search(r"All Finished", text, re.IGNORECASE):
        return "no violations"
    return None


def summarize(
    language: str,
    action: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> str:
    """One-line summary; never raises on unexpected output."""
    text = _tail(stdout, stderr)
    candidates = {
        "python": (_pytest, _unittest),
        "javascript": (_jest_like,),
        "typescript": (_jest_like,),
        "java": (_maven_gradle,),
        "csharp": (_dotnet,),
        "cpp": (_ctest,),
        "sql": (_sqlfluff,),
        "go": (_go,),
        "php": (_phpunit,),
        "rust": (_cargo,),
    }.get(language, ())
    for parser in candidates:
        try:
            summary = parser(text)
        except (TypeError, ValueError, re.error):
            summary = None
        if summary:
            return summary
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    tail = " | ".join(lines[-2:])[:300]
    if tail:
        return tail
    return f"exit code {exit_code}"
