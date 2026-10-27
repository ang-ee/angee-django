"""Optional IAM donor preserves source state through native app population."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("order", ["iam-first", "provenance-first", "no-iam", "iam-only"])
def test_iam_principal_donor_is_optional_and_order_independent(order: str) -> None:
    script = '''
import os
import sys
os.environ.pop("DJANGO_SETTINGS_MODULE", None)
from django.apps import AppConfig
from django.conf import settings
class BareGraphQL(AppConfig):
    name = "angee.graphql"
    label = "graphql"
selected = {
    "iam-first": ["angee.iam", "angee.provenance"],
    "provenance-first": ["angee.provenance", "angee.iam"],
    "no-iam": ["angee.provenance"],
    "iam-only": ["angee.iam"],
}[sys.argv[1]]
settings.configure(
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "rebac", "simple_history",
                    "angee", "__main__.BareGraphQL", *selected],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    SECRET_KEY="optional-iam-donor", ANGEE_CHANGESET_MODEL="provenance.Changeset",
)
import django
django.setup()
from django.apps import apps
from django.db.migrations.state import ModelState
if sys.argv[1] == "iam-only":
    from angee.iam.models import User
    assert not hasattr(User, "statement_source")
    assert not any(name == "angee.provenance" or name.startswith("angee.provenance.") for name in sys.modules)
    raise SystemExit(0)
from angee.provenance import models as provenance
if sys.argv[1] == "no-iam":
    assert not hasattr(provenance, "UserSource")
    assert "angee.iam.models" not in sys.modules
else:
    from angee.compose.model_composition import ModelComposition
    from angee.iam.models import User
    donor = provenance.UserSource
    composition = ModelComposition.discover((apps.get_app_config("iam"), apps.get_app_config("provenance")))
    assert composition.donors(User) == (donor,)
    from django.test.utils import isolate_apps
    with isolate_apps():
        class BaselineUser(User):
            class Meta(User.Meta):
                abstract = False
                app_label = "iam"
        class ComposedUser(donor, User):
            class Meta(User.Meta):
                abstract = False
                app_label = "iam"
        def fields(model):
            return {name: field.deconstruct()[1:]
                    for name, field in ModelState.from_model(model).fields.items()}
        assert fields(ComposedUser) == fields(BaselineUser)
        for kind in ("person", "service"):
            for active in (False, True):
                user = ComposedUser(kind=kind, is_active=active)
                assert user.source_kind == ("person" if kind == "person" and active else None)
'''
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(root / "addons")))}
    result = subprocess.run(
        [sys.executable, "-c", script, order], cwd=root, env=environment,
        text=True, capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
