"""A checked resource target retains its native model type through resolution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_typed_xref_resolution_preserves_optional_and_wrong_assignment_errors(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    caller = tmp_path / "typed_resource_caller.py"
    caller.write_text(
        "from typing import assert_type\n"
        "from django.apps import apps\n"
        "from django.db import models\n"
        "from angee.money.models import Currency\n"
        "from angee.resources.models import Resource\n"
        "from angee.resources.widgets import resolve_ledger_xref, resolve_xref\n"
        "from typing_runtime.money.models import Currency as ConcreteCurrency\n"
        "currency_model = apps.get_model('money', 'Currency')\n"
        "assert_type(resolve_ledger_xref('money.EUR', model=currency_model), ConcreteCurrency | None)\n"
        "assert_type(resolve_ledger_xref('money.EUR'), models.Model | None)\n"
        "assert_type(Resource.objects.resolve('money.EUR', model=currency_model), ConcreteCurrency)\n"
        "assert_type(Resource.objects.resolve('money.EUR'), models.Model)\n"
        "assert_type(resolve_xref('money.EUR', Resource, {}, model=currency_model), ConcreteCurrency)\n"
        "wrong_optional: Currency = resolve_ledger_xref('money.EUR', model=currency_model)\n"
        "wrong_model: int = Resource.objects.resolve('money.EUR', model=currency_model)\n"
    )
    env = dict(os.environ)
    env.pop("ANGEE_PROJECT_DIR", None)
    env["PYTHONPATH"] = str(root)
    result = subprocess.run([
        sys.executable, "-m", "mypy", "--config-file", str(root / "pyproject.toml"),
        "--cache-dir", str(tmp_path / "cache"), str(caller),
    ], cwd=root, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count(": error:") == 2, result.stdout
    assert "typed_resource_caller.py:14: error:" in result.stdout
    assert "typed_resource_caller.py:15: error:" in result.stdout
