import pytest

from fdsx.core.engine import run_flow
from fdsx.display.terminal import display_completion_summary
from tests import FIXTURES_DIR


@pytest.mark.parametrize("quiet", [False, True])
def test_completion_shows_route_on_stderr(tmp_path, monkeypatch, capsys, quiet):
    monkeypatch.chdir(tmp_path)

    result = run_flow(FIXTURES_DIR / "simple_flow.yaml", base_dir=tmp_path, quiet=quiet)

    captured = capsys.readouterr()
    assert result.status == "completed"
    assert "Route: plan → implement → review\n" in captured.err
    assert "Route:" not in captured.out


def test_loop_route_preserves_repeated_visits_and_choices(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)

    result = run_flow(FIXTURES_DIR / "loop_flow.yaml", base_dir=tmp_path, quiet=True)

    captured = capsys.readouterr()
    route = next(
        line for line in captured.err.splitlines() if line.startswith("Route:")
    )
    assert result.status == "max_loop_reached"
    assert "plan → implement → review → decide → plan" in route
    assert "done" not in route


def test_aborted_flow_shows_route(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "abort.yaml"
    path.write_text(
        "name: abort-test\n"
        "description: Test route on abort\n"
        "start_at: stop\n"
        "states:\n"
        "  stop:\n"
        "    type: fail\n"
        "    error: Stopped\n"
        "    cause: Test stop\n"
    )

    result = run_flow(path, base_dir=tmp_path, quiet=True)

    assert result.status == "aborted"
    assert "Route: stop\n" in capsys.readouterr().err


@pytest.mark.parametrize("route", [None, []])
def test_missing_route_omits_extra_line(capsys, route):
    display_completion_summary("flow", 1.0, route=route)
    assert "Route:" not in capsys.readouterr().err


def test_route_sanitizes_state_names(capsys):
    display_completion_summary("flow", 1.0, route=["plan\x1b[31m", "done"])
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "Route: plan → done" in captured.err
