"""Native runtime-injected history save method keeps Django's own save type."""

import subprocess
import sys
from pathlib import Path


def test_capture_only_history_save_has_the_native_model_signature(tmp_path):
    source = """
from typing import assert_type
from angee.provenance.engine.history import CaptureHistoryMixin

def save(row: CaptureHistoryMixin) -> None:
    assert_type(row.save_without_historical_record(using="default", update_fields=["title"]), None)
"""
    fixture = tmp_path / "history_save.py"
    fixture.write_text(source)
    command = [sys.executable, "-m", "mypy", "--config-file", str(Path(__file__).with_name("mypy.toml")), str(fixture)]
    accepted = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    fixture.write_text(
        source
        + """
def invalid(row: CaptureHistoryMixin) -> None:
    row.save_without_historical_record(using=1)
    row.save_without_historical_record(unknown=True)
"""
    )
    refused = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert refused.returncode != 0
    assert refused.stdout.count("error:") == 2, refused.stdout + refused.stderr
    assert '"using"' in refused.stdout
    assert '"unknown"' in refused.stdout
