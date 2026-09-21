"""Offline CLI smoke checks. Run with uv run python scripts/verify_concurrent_map.py."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/concurrent_map"


def wait_for(predicate, description):
    deadline = time.monotonic() + 15
    while not predicate():
        if time.monotonic() > deadline:
            raise RuntimeError(f"Timed out: {description}")
        time.sleep(0.01)


def verify(form, scenario, folder):
    folder.mkdir()
    (folder / ".fdsx").mkdir()
    env = {**os.environ, "XDG_CONFIG_HOME": str(folder / "xdg")}
    run_dir = folder / ".fdsx/runs/smoke"
    progress = run_dir / "work/progress.json"
    log = folder / "cli.log"
    helper = str(FIXTURES / "item.sh")
    workflow = str(FIXTURES / f"{form}.yaml")
    command = [
        sys.executable,
        "-m",
        "fdsx.cli.main",
        "run",
        workflow,
        "--thread-id",
        "smoke",
        "--input",
        f"helper={helper}",
    ]
    if scenario == "failure":
        (folder / "fail-B").touch()

    def exists(name):
        return (folder / name).exists()

    def release(item):
        (folder / f"release-{item}").touch()

    def saved():
        return json.loads(progress.read_text())["items"] if progress.exists() else {}

    with log.open("w") as output:
        proc = subprocess.Popen(
            command, cwd=folder, env=env, stdout=output, stderr=output
        )
        try:
            wait_for(
                lambda: exists("A.start") and exists("B.start"), "two concurrent items"
            )
            assert not exists("C.start") and not exists("D.start")
            release("B")
            if scenario == "failure":
                wait_for(
                    lambda: "[iter-2/4] ✗ failed" in log.read_text(),
                    "terminal failure detected",
                )
                assert proc.poll() is None and not exists("A.end")
                assert not exists("C.start")
                release("A")
                assert proc.wait(timeout=15) != 0
                assert set(saved()) == {"0"}
                assert not exists("C.start") and not exists("D.start")
                (folder / "fail-B").unlink()
            else:
                wait_for(lambda: exists("C.start"), "refill while A is blocked")
                assert not exists("A.end") and not exists("D.start")
                assert "1" in saved() and "0" not in saved()
                release("C")
                wait_for(lambda: exists("D.start"), "second refill")
                release("D")
                wait_for(
                    lambda: set(saved()) == {"1", "2", "3"},
                    "out-of-order durable results",
                )
                assert not exists("A.end")
                if scenario == "interrupt":
                    proc.send_signal(signal.SIGINT)
                    assert proc.wait(timeout=15) == 130
                    assert (
                        json.loads((run_dir / "run.json").read_text())["status"]
                        == "interrupted"
                    )
                    assert not (folder / ".fdsx/locks/smoke.lock").exists()
                    pid = int((folder / "A.start").read_text())
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise AssertionError("interrupted item process survived")
                else:
                    release("A")
                    assert proc.wait(timeout=15) == 0
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=15)
    if scenario != "success":
        starts = {
            item: (folder / f"{item}.start").read_text()
            for item in "ABCD"
            if exists(f"{item}.start")
        }
        for item in "ABCD":
            release(item)
        with log.open("a") as output:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fdsx.cli.main",
                    "resume",
                    "--thread-id",
                    "smoke",
                ],
                cwd=folder,
                env=env,
                stdout=output,
                stderr=output,
                timeout=20,
            )
        assert result.returncode == 0, log.read_text()
        reused = "A" if scenario == "failure" else "BCD"
        assert all(
            (folder / f"{item}.start").read_text() == starts[item] for item in reused
        )
    final = saved()
    assert set(final) == {"0", "1", "2", "3"}
    values = [final[str(i)]["result"] for i in range(4)]
    if form == "local":
        assert [value["index"] for value in values] == list(range(4))
        values = [value["output"] for value in values]
    assert values == [f"result-{item}" for item in "ABCD"]
    return {"form": form, "scenario": scenario, "observed_limit": 2, "result": "passed"}


def main():
    # mkdtemp intentionally retains evidence for inspection; all runtime data
    # stays here, never in the repository or a user's configured workspace.
    root = Path(tempfile.mkdtemp(prefix="fdsx-concurrent-map-"))
    results = [
        verify(form, scenario, root / f"{form}-{scenario}")
        for form in ("legacy", "local")
        for scenario in ("success", "failure", "interrupt")
    ]
    (root / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps({"artifacts": str(root), "checks": results}, indent=2))


if __name__ == "__main__":
    main()
