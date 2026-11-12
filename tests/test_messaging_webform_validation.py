"""Exercise a later consumer Channel donor in a freshly composed HTTP host."""

from __future__ import annotations

from pathlib import Path

import pytest

from angee.base.testing.packages import write_addon_distribution
from tests.composed_host import run_composed_tests


def test_composed_webform_validation(tmp_path: Path, subtests: pytest.Subtests) -> None:
    addon = tmp_path / "addons" / "example" / "webform_semantics"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(addon , 'example.webform_semantics', depends_on=('angee.messaging',))
    (addon / "models.py").write_text(
        "from datetime import date\n"
        "from django.core.exceptions import ValidationError\n"
        "from django_compose.models import Composable\n"
        "\n"
        "class ChannelSemanticValidation(Composable, extends='messaging.Channel'):\n"
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
    run_composed_tests(
        tmp_path, subtests, app=("example.webform_semantics", "angee.intake"),
        sqlite=("tests.native_messaging_webform_validation",), addon_dirs=(tmp_path / "addons",),
    )
