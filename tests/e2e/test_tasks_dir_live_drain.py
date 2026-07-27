import subprocess
import sys
import time
from pathlib import Path

import yaml

from fdsx.models.task import TaskEntry, TaskFile, save_task_file


def test_second_runner_cannot_drain_the_same_tasks_directory(tmp_path: Path) -> None:
    (tmp_path / ".fdsx").mkdir()
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    save_task_file(
        tasks_dir / "001-task.yaml",
        TaskFile(entries=[TaskEntry(description="task")]),
    )
    workflow = tmp_path / "blocking-flow.yaml"
    workflow.write_text(
        yaml.safe_dump(
            {
                "name": "Blocking flow",
                "description": "Keeps a tasks-directory runner active",
                "start_at": "block",
                "states": {
                    "block": {
                        "type": "task",
                        "provider": "system",
                        "command": (
                            "touch runner-started; "
                            "while [ ! -f release-runner ]; do sleep 0.05; done"
                        ),
                        "result_path": "$.result",
                        "end": True,
                    }
                },
            }
        )
    )
    command = [
        sys.executable,
        "-m",
        "fdsx.cli.main",
        "--ci",
        "run",
        str(workflow),
        "--tasks-dir",
        str(tasks_dir),
        "--auto-workflow",
    ]
    first = subprocess.Popen(
        command,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "runner-started").exists():
            if first.poll() is not None:
                _, stderr = first.communicate()
                raise AssertionError(f"First runner exited early: {stderr}")
            if time.monotonic() >= deadline:
                raise AssertionError("First runner did not start in time")
            time.sleep(0.05)

        second = subprocess.Popen(
            command,
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _, second_stderr = second.communicate(timeout=5)
        except subprocess.TimeoutExpired as exc:
            second.kill()
            second_stdout, second_stderr = second.communicate()
            raise AssertionError(
                "Second runner did not reject the tasks-directory lock within "
                f"5 seconds.\nstdout:\n{second_stdout}\nstderr:\n{second_stderr}"
            ) from exc
        assert second.returncode == 1
        assert "already being drained by PID" in second_stderr
    finally:
        (tmp_path / "release-runner").touch()
        if second is not None and second.poll() is None:
            second.communicate(timeout=5)
        first_stdout, first_stderr = first.communicate(timeout=5)

    assert first.returncode == 0, f"{first_stdout}\n{first_stderr}"
