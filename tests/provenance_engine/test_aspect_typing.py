"""Model-specific policies bind one subject through native assertion declarations."""

import subprocess
import sys
from pathlib import Path


def test_aspect_and_declaration_preserve_the_callback_subject(tmp_path):
    source = """
from collections.abc import Sequence
from typing import Any, assert_type
from django.db import models
from angee.provenance.engine.statements import Aspect, StatementRecords

class Owner(models.Model):
    def choose(self, rows: Sequence[Any]) -> Sequence[Any]:
        return rows
    def apply_choice(self, row: Any) -> None:
        pass

class Other(models.Model):
    def choose(self, rows: Sequence[Any]) -> Sequence[Any]:
        return rows
    def apply_choice(self, row: Any) -> None:
        pass

aspect = Aspect(candidates=Owner.choose, apply=Owner.apply_choice)
assert_type(aspect, Aspect[Owner])
declaration = StatementRecords(aspects={"choice": aspect, "plain": Aspect(fields=("id",))})
assert_type(declaration, StatementRecords[Owner])
aspect.resolve(Owner(), [])
"""
    fixture = tmp_path / "subject_callbacks.py"
    fixture.write_text(source)
    command = [sys.executable, "-m", "mypy", "--config-file", str(Path(__file__).with_name("mypy.toml")), str(fixture)]
    accepted = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    fixture.write_text(
        source
        + """
Aspect[Owner](candidates=Other.choose)
Aspect[Owner](apply=Other.apply_choice)
aspect.resolve(Other(), [])
StatementRecords[Owner](aspects={"wrong": Aspect[Other](candidates=Other.choose)})
"""
    )
    refused = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert refused.returncode != 0
    assert refused.stdout.count("error:") == 4, refused.stdout + refused.stderr
    assert '"candidates"' in refused.stdout
    assert '"apply"' in refused.stdout
    assert '"resolve"' in refused.stdout
    assert "Dict entry" in refused.stdout
