"""Exercise a later consumer Channel donor in a freshly composed HTTP host."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_composed_webform_validation(tmp_path: Path) -> None:
    addon = tmp_path / "addons" / "example" / "webform_semantics"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    (addon / "addon.toml").write_text(
        '[addon]\nname = "example.webform_semantics"\n'
        'description = "Webform semantic validation test donor"\n'
        'depends_on = ["angee.messaging"]\n'
    )
    (addon / "models.py").write_text(
        "from datetime import date\n"
        "from django.core.exceptions import ValidationError\n"
        "from django.db import models\n"
        "\n"
        "class ChannelSemanticValidation(models.Model):\n"
        "    extends = 'messaging.Channel'\n"
        "\n"
        "    class Meta:\n"
        "        abstract = True\n"
        "\n"
        "    def validate_webform_answers(self, answers):\n"
        "        super().validate_webform_answers(answers)\n"
        "        if self.slug != 'request-form':\n"
        "            return\n"
        "        errors = {}\n"
        "        if 'when' in answers:\n"
        "            try:\n"
        "                date.fromisoformat(answers['when'])\n"
        "            except ValueError:\n"
        "                errors['when'] = 'Enter a calendar date.'\n"
        "        if 'amount' in answers and answers['amount'] <= 0:\n"
        "            errors['amount'] = 'Enter a positive number.'\n"
        "        if errors:\n"
        "            raise ValidationError(errors)\n"
    )
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "composed-webform.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests" / "composed_host.py"),
            "--runtime-dir", str(tmp_path / "runtime"),
            "--addon-dir", str(tmp_path / "addons"),
            "--app", "example.webform_semantics",
            "--app", "angee.intake",
            "--no-examples",
            "--action", "tests",
            "--test-label", "tests.native_messaging_webform_validation",
            "--output", str(report),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, f"Composed webform tests failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "sqlite"}
