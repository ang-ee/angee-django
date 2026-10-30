"""Stage consumers retain their own stage type through the shared primitive."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_stage_contract_retains_the_consumers_stage_type(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "stage_types.py"
    declarations = """\
from typing import assert_type
from django.db.models import Model, QuerySet
from angee.base.models.stages import Stage, StagedModelMixin

class DeliveryStage(Stage):
    class Meta:
        abstract = True

class DeliveryRecord(StagedModelMixin[DeliveryStage]):
    class Meta:
        abstract = True

def inspect(container: Model, record: DeliveryRecord, stages: QuerySet[DeliveryStage]) -> None:
    assert_type(DeliveryStage.for_container(container), QuerySet[DeliveryStage, DeliveryStage])
    assert_type(DeliveryStage.resolve_default(container), DeliveryStage | None)
    assert_type(DeliveryStage.resolve_default_fallback(stages), DeliveryStage | None)
    assert_type(DeliveryRecord.stage_model(), type[DeliveryStage])
    assert_type(record.resolve_default_stage(), DeliveryStage | None)
"""
    source.write_text(declarations)
    command = [
        sys.executable, "-m", "mypy", "--config-file", str(root / "tests/typing_app/mypy.ini"), str(source),
    ]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(root / "src")))}
    positive = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
    assert positive.returncode == 0, positive.stdout + positive.stderr

    invalid = """\
    record.resolve_default_stage().name
    DeliveryRecord.stage_model().missing_method()
    incompatible: str = DeliveryStage.resolve_default(container)
"""
    source.write_text(declarations + invalid)
    negative = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    for line in range(len(declarations.splitlines()) + 1, len((declarations + invalid).splitlines()) + 1):
        assert f"stage_types.py:{line}: error:" in negative.stdout, negative.stdout
