"""Execute the bundled bridge with a fake native module, never a Pi binary."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import fdsx.providers.pi


@pytest.mark.parametrize("failure", [False, True])
def test_bridge_selects_native_endpoint_and_sanitizes_errors(tmp_path, failure):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional bridge syntax fixture requires local node")
    bridge = Path(fdsx.providers.pi.__file__).with_name("pi_fork.ts").read_text()
    # The production bridge is JavaScript-compatible TypeScript. Replace only
    # module resolution; execute its actual preparation and error handling.
    (tmp_path / "bridge.mjs").write_text(
        bridge.replace('"@earendil-works/pi-coding-agent"', '"./native.mjs"')
    )
    (tmp_path / "native.mjs").write_text("""
import { appendFileSync } from "node:fs";
const record = (operation, ...args) => appendFileSync("calls.jsonl", JSON.stringify([operation, ...args]) + "\\n");
export class SessionManager {
  static open(path) {
    record("open", path);
    return { getSessionId: () => "source-id", getEntry: (id) => ({id}) };
  }
  static forkFrom(path, cwd, directory) {
    record("forkFrom", path, cwd, directory);
    if (process.env.FAIL_FIXTURE === "yes") throw new Error("PRIVATE conversation");
    return {
      createBranchedSession: (id) => record("createBranchedSession", id),
      getSessionFile: () => "/child/native.jsonl",
      getBranch: () => [{type:"message", id:"completed"}, {type:"label", id:"regenerated-label"}]
    };
  }
}
""")
    (tmp_path / "run.mjs").write_text(
        'import bridge from "./bridge.mjs"; bridge(); throw new Error("must exit before prompting");'
    )
    result = subprocess.run(
        [node, str(tmp_path / "run.mjs")],
        cwd=tmp_path,
        env={
            **os.environ,
            "FAIL_FIXTURE": "yes" if failure else "no",
            "FDSX_PI_FORK_REQUEST": json.dumps(
                {
                    "path": "/source/native.jsonl",
                    "id": "source-id",
                    "endpoint": "completed",
                    "directory": "/child",
                }
            ),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    if failure:
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == "Native Pi endpoint fork failed\n"
    else:
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"path": "/child/native.jsonl"}
        calls = [
            json.loads(line)
            for line in (tmp_path / "calls.jsonl").read_text().splitlines()
        ]
        assert calls == [
            ["open", "/source/native.jsonl"],
            ["forkFrom", "/source/native.jsonl", str(tmp_path), "/child"],
            ["createBranchedSession", "completed"],
        ]
    assert "PRIVATE" not in result.stderr
