"""Desktop endpoint for diagram artifacts shown in the graphical chat."""

from pathlib import Path

import pytest

from satellite.desktop.backend import BackendError, DesktopBackend


def _backend(tmp_path: Path) -> DesktopBackend:
    return DesktopBackend(
        tmp_path,
        home=tmp_path / "home",
        recents_path=tmp_path / "recents.json",
    )


def test_diagram_svg_is_served_from_the_project(tmp_path: Path) -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>hola</text></svg>'
    target = tmp_path / "diagrams" / "flow.svg"
    target.parent.mkdir(parents=True)
    target.write_text(svg, encoding="utf-8")

    backend = _backend(tmp_path)

    served = backend.diagram_svg("diagrams/flow.svg")
    assert served["svg"] == svg
    assert served["path"] == "diagrams/flow.svg"
    # The absolute path stored in the conversation works too.
    assert backend.diagram_svg(str(target))["svg"] == svg


def test_diagram_outside_the_project_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.svg"
    outside.write_text("<svg/>", encoding="utf-8")
    try:
        with pytest.raises(BackendError) as exc:
            _backend(tmp_path).diagram_svg(str(outside))
        assert exc.value.code == "diagram_outside_project"
    finally:
        outside.unlink(missing_ok=True)


def test_non_svg_and_invalid_files_are_rejected(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    txt = tmp_path / "note.txt"
    txt.write_text("hola", encoding="utf-8")
    with pytest.raises(BackendError) as exc:
        backend.diagram_svg("note.txt")
    assert exc.value.code == "diagram_not_svg"

    fake = tmp_path / "fake.svg"
    fake.write_text("<html>no svg</html>", encoding="utf-8")
    with pytest.raises(BackendError) as exc:
        backend.diagram_svg("fake.svg")
    assert exc.value.code == "diagram_invalid"

    with pytest.raises(BackendError) as exc:
        backend.diagram_svg("missing.svg")
    assert exc.value.code == "diagram_not_found"
