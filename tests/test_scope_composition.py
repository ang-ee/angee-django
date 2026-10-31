"""Neutral scope contracts participate through native selected model ancestry."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("conflict", [None, "direct", "indirect", "mixed"])
@pytest.mark.parametrize("defaulted_first", [False, True])
def test_scope_participation_without_optional_api_or_protection(
    tmp_path: Path, provider: bool, conflict: str | None, defaulted_first: bool,
) -> None:
    """Independent and external participants opt in; ordinary records stay unchanged."""
    sources = {
        "participants": '''
from angee.base.models.scope import DefaultedScopeMixin, NullableScopeMixin
class First(NullableScopeMixin, concrete=True):
    class Meta:
        abstract = True
class Second(DefaultedScopeMixin, concrete=True):
    class Meta:
        abstract = True
class Child(NullableScopeMixin, inherits="participants.First"):
    class Meta:
        abstract = True
class Unrelated(Composable, concrete=True):
    class Meta:
        abstract = True
''',
        "thirdparty": '''
from angee.base.models.scope import NullableScopeMixin
class External(NullableScopeMixin, concrete=True):
    class Meta:
        abstract = True
''',
    }
    if provider:
        sources["provider"] = '''
class Company(Composable, concrete=True):
    class Meta:
        abstract = True
class ScopeField(models.Model):
    company = models.ForeignKey("provider.Company", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="+")
    class Meta:
        abstract = True
class CreationDefault(ScopeField):
    def creation_default(self):
        return "defaulting-enabled"
    class Meta:
        abstract = True
class Nullable(ScopeField, Composable, extends="base.NullableScopeMixin"):
    class Meta:
        abstract = True
class Defaulted(CreationDefault, Composable, extends="base.DefaultedScopeMixin"):
    class Meta:
        abstract = True
'''
    if conflict:
        nullable = "NullableScopeMixin"
        defaulted = "DefaultedScopeMixin"
        if conflict in {"indirect", "mixed"}:
            sources["participants"] += """
class NullableParent(NullableScopeMixin):
    class Meta:
        abstract = True
class DefaultedParent(DefaultedScopeMixin):
    class Meta:
        abstract = True
"""
            nullable = "NullableParent"
            if conflict == "indirect":
                defaulted = "DefaultedParent"
        bases = (defaulted, nullable) if defaulted_first else (nullable, defaulted)
        sources["participants"] += f"""
class Contradictory({", ".join(bases)}, concrete=True):
    class Meta:
        abstract = True
"""
    for name, source in sources.items():
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").touch()
        (package / "models.py").write_text(
            "from django.db import models\nfrom django_compose.models import Composable\n" + source,
        )
    script = f'''
from pathlib import Path
import importlib
from django.conf import settings
settings.configure(
    INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "rebac", "angee.base", *{list(sources)!r}],
    DATABASES={{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}}},
    SECRET_KEY="scope-contract", DEFAULT_AUTO_FIELD="django.db.models.AutoField",
)
import django
django.setup()
from django.apps import apps
from django.db import models
from django.db.migrations.state import ModelState
from django_compose.model_composition import ModelComposition
from django_compose.rendering import render_models
composition = ModelComposition.discover(apps.get_app_configs())
assert "base.nullablescopemixin" not in composition.models_by_label
assert "base.defaultedscopemixin" not in composition.models_by_label
root = Path("runtime")
root.mkdir()
(root / "__init__.py").touch()
for label in composition.labels:
    package = root / label
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "models.py").write_text(render_models(composition, label))
for model in composition.ordered_models:
    importlib.import_module(f"runtime.{{model._meta.app_label}}.models")
for label in ("participants.First", "participants.Second", "thirdparty.External"):
    model = apps.get_model(label)
    fields = ModelState.from_model(model).fields
    assert ("company" in fields) is {provider!r}, (label, fields)
    assert model._meta.db_table == label.replace(".", "_").lower()
    if {provider!r}:
        field = model._meta.get_field("company")
        assert field.null and field.blank and field.remote_field.on_delete is models.PROTECT
        assert field.column == "company_id"
        assert field.related_model is apps.get_model("provider.Company")
child = apps.get_model("participants.Child")
first = apps.get_model("participants.First")
assert child._meta.parents == {{first: child._meta.get_field("first_ptr")}}
assert set(ModelState.from_model(child).fields) == {{"first_ptr"}}
assert "company" not in ModelState.from_model(apps.get_model("participants.Unrelated")).fields
if {provider!r}:
    assert apps.get_model("participants.Second")().creation_default() == "defaulting-enabled"
    assert not hasattr(first, "creation_default")
    assert child._meta.get_field("company").model is first
    assert "company" not in ModelState.from_model(apps.get_model("provider.Company")).fields
assert not apps.is_installed("angee.graphql")
assert not apps.is_installed("angee.iam")
'''
    paths = (str(tmp_path), str(Path(__file__).resolve().parents[1] / "src"), os.environ.get("PYTHONPATH", ""))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60,
    )
    if conflict:
        assert result.returncode != 0
        assert "cannot compose both NullableScopeMixin and DefaultedScopeMixin" in result.stderr, result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
