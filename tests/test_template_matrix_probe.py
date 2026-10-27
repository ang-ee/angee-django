"""Exercise external template-probe outcomes without contacting remote services."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[1] / "scripts" / "template-matrix-probe.sh"


@pytest.mark.parametrize("failure", ["", "render", "image", "source"])
def test_matrix_probe_propagates_failures_and_cleans_its_scratch(tmp_path: Path, failure: str) -> None:
    """Every real check affects the exit status; the probe owns its scratch lifetime."""

    binaries = tmp_path / "bin"
    binaries.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls = tmp_path / "calls"
    commands = {
        "angee": '''#!/bin/bash
printf '%s\\n' "angee $*" >> "$PROBE_CALLS"
if [[ "$PROBE_FAILURE" = render && "$2" = */dev-process-base ]]; then
  echo 'mock render failed' >&2
  exit 1
fi
cat > "$2/angee.yaml" <<'YAML'
services:
  app:
    image: example.invalid/image:latest
sources:
  app:
    repo: https://example.invalid/source.git
YAML
''',
        "docker": '''#!/bin/bash
printf '%s\\n' "docker $*" >> "$PROBE_CALLS"
[[ "$PROBE_FAILURE" != image ]]
''',
        "git": '''#!/bin/bash
printf '%s\\n' "git $*" >> "$PROBE_CALLS"
[[ "$PROBE_FAILURE" != source ]]
''',
    }
    for name, source in commands.items():
        command = binaries / name
        command.write_text(source)
        command.chmod(0o755)
    result = subprocess.run(
        ["bash", str(PROBE), str(tmp_path / "registry")],
        env={
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "TMPDIR": str(scratch),
            "PROBE_CALLS": str(calls),
            "PROBE_FAILURE": failure,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if failure else 0), result.stdout + result.stderr
    assert ("MATRIX PROBE: ALL GREEN" in result.stdout) is (not failure)
    if failure == "render":
        assert "dev-process-base: render FAILED: mock render failed" in result.stdout
    invoked = calls.read_text().splitlines()
    assert sum(command.startswith("angee init ") for command in invoked) == 7
    assert "docker manifest inspect example.invalid/image:latest" in invoked
    assert "git ls-remote --heads https://example.invalid/source.git" in invoked
    assert list(scratch.iterdir()) == []
