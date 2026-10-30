"""Serialized state fields keep native dynamic enums and precise nullability."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.db import models

from angee.base.fields.state import StateField


@pytest.mark.parametrize("nullable", [False, True])
def test_serialized_state_field_reconstructs_native_dynamic_enum(nullable: bool) -> None:
    class Status(models.TextChoices):
        ENABLED = "enabled", "Enabled"

    original = StateField(choices_enum=Status, null=nullable, default=Status.ENABLED, db_index=False)
    name, _, args, kwargs = original.deconstruct()
    assert "choices_enum" not in kwargs
    reconstructed = StateField(*args, **kwargs)

    assert reconstructed.null is nullable
    assert reconstructed.choices_enum is not Status
    assert issubclass(reconstructed.choices_enum, models.TextChoices)
    assert isinstance(reconstructed.to_python("enabled"), reconstructed.choices_enum)
    assert reconstructed.to_python("enabled").value == Status.ENABLED.value
    assert reconstructed.deconstruct() == (name, original.deconstruct()[1], args, kwargs)
    with pytest.raises(ValidationError):
        reconstructed.to_python("unknown")


def test_state_field_constructor_and_descriptor_typing(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "state_typing_settings.py").write_text(
        "SECRET_KEY = 'state-field-types'\nINSTALLED_APPS = []\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
    )
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.base.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'addons'}\ncache_dir = {tmp_path / 'cache'}\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = state_typing_settings\n"
    )
    declarations = '''\
from typing import assert_type
from django.db import models
from django.utils.translation import gettext_lazy
from angee.base.fields import StateField

class Status(models.TextChoices):
    ENABLED = 'enabled', 'Enabled'

class Other(models.TextChoices):
    OTHER = 'other', 'Other'

assert_type(StateField(choices_enum=Status), StateField[Status])
assert_type(StateField(choices_enum=Status, null=True), StateField[Status | None])
assert_type(StateField(choices=[('enabled', 'Enabled')]), StateField[models.TextChoices])
assert_type(StateField(choices=[('enabled', gettext_lazy('Enabled'))], choices_enum=None, null=False),
            StateField[models.TextChoices])
assert_type(StateField(choices=[(None, 'None'), ('enabled', 'Enabled')], null=True),
            StateField[models.TextChoices | None])

class Row(models.Model):
    exact = StateField(choices_enum=Status)
    optional_exact = StateField(choices_enum=Status, null=True)
    reconstructed = StateField(choices=[('enabled', 'Enabled')])
    optional_reconstructed = StateField(choices=[('enabled', 'Enabled')], null=True)
    class Meta:
        app_label = 'state_typing'

def proof(row: Row) -> None:
    assert_type(row.exact, Status)
    assert_type(row.optional_exact, Status | None)
    assert_type(row.reconstructed, models.TextChoices)
    assert_type(row.optional_reconstructed, models.TextChoices | None)
    row.exact = Status.ENABLED
    row.optional_exact = None
    row.reconstructed = Status.ENABLED
    row.optional_reconstructed = None
'''
    positive = tmp_path / "positive.py"
    positive.write_text(declarations)
    negative = tmp_path / "negative.py"
    negative.write_text(declarations + '''\
def rejected(row: Row) -> None:
    row.exact = Other.OTHER
    row.exact = None
    row.reconstructed = None
    row.optional_reconstructed = 'enabled'
    StateField(choices_enum=str)
    StateField(choices=[('enabled', 'Enabled')], null='yes')
''')
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(tmp_path), str(root), str(root / "addons"), env.get("PYTHONPATH")))
    )
    env["DJANGO_SETTINGS_MODULE"] = "state_typing_settings"
    command = [sys.executable, "-m", "mypy", "--config-file", str(config)]

    def check(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*command, str(path)], cwd=root, env=env, capture_output=True, text=True, check=False)

    valid = check(positive)
    assert valid.returncode == 0, valid.stdout + valid.stderr
    invalid = check(negative)
    assert invalid.returncode == 1, invalid.stdout + invalid.stderr
    assert invalid.stdout.count(": error:") == 6, invalid.stdout
    assert "[assignment]" in invalid.stdout
    assert "[call-overload]" in invalid.stdout or "[type-var]" in invalid.stdout
