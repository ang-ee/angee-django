"""Generic provenance runs as a native Django app without Angee or REBAC models."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("operation", [
    "explicit-source", "separate-history", "bound-history", "failed-bulk", "protected-delete",
])
def test_native_capture_preserves_database_owners_without_host_imports(tmp_path: Path, operation: str) -> None:
    _capture_database_owner_contract(tmp_path, operation)


def test_angee_retained_references_follow_history_routing_when_capture_is_disabled(tmp_path: Path) -> None:
    """Angee's merge contract reads retained versions through the history router."""

    _capture_database_owner_contract(tmp_path, "retained-reference", angee_adapter=True)


def _capture_database_owner_contract(tmp_path: Path, operation: str, *, angee_adapter: bool = False) -> None:
    package = tmp_path / "native_capture"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "models.py").write_text("""
from django.db import models
from angee.provenance.context import ChangesetRequired
from angee.provenance.history import HistoryMixin, ModelHistory

class Changeset(models.Model):
    actor_ref = models.TextField(blank=True)
    change_reason = models.TextField(blank=True)
    touched = models.JSONField(default=list)
    counts = models.JSONField(default=dict)
    occurred_at = models.DateTimeField()
    required_context_error = ChangesetRequired

    @classmethod
    def ambient_attribution(cls):
        return "", ""

    @classmethod
    def context_required(cls):
        return True

    def history_user(self):
        return None

    def close(self, outcome):
        pass

class Container(models.Model):
    pass

class Row(HistoryMixin):
    value = models.CharField(max_length=40, unique=True)
    container = models.ForeignKey(Container, null=True, on_delete=models.CASCADE)
    history = ModelHistory(inherit=True, use_base_model_db=False)

class RetainedRow(models.Model):
    row = models.ForeignKey(Row, on_delete=models.PROTECT)
""")
    if angee_adapter:
        models_path = package / "models.py"
        models_path.write_text(models_path.read_text().replace(
            "from angee.provenance.history import HistoryMixin, ModelHistory",
            "from angee.provenance.contrib.angee.history import HistoryMixin, ModelHistory",
        ).replace("use_base_model_db=False", "use_base_model_db=True"))
    script = """
import os
import sys
from contextlib import nullcontext
os.environ.pop("DJANGO_SETTINGS_MODULE", None)
from django.conf import settings

class StorageRouter:
    history_alias = "secondary"

    def db_for_read(self, model, **hints):
        if model._meta.app_label == "native_capture":
            if sys.argv[1] in {"failed-bulk", "protected-delete"}:
                return "default"
            if model._meta.model_name == "changeset":
                return "secondary"
            if sys.argv[1] == "retained-reference" and model._meta.model_name == "historicalrow":
                return "secondary"
            return "default"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == "native_capture":
            if sys.argv[1] in {"failed-bulk", "protected-delete"}:
                return "default"
            if model._meta.model_name in {"row", "container"}:
                return "secondary" if sys.argv[1] == "retained-reference" else "default"
            if model._meta.model_name == "changeset":
                return "secondary"
            return self.history_alias
        return None

    def allow_relation(self, first, second, **hints):
        if first._meta.app_label == second._meta.app_label == "native_capture":
            return True
        return None

settings.configure(
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "simple_history",
                    *(["rebac", "angee.base"] if sys.argv[2] == "angee" else []), "native_capture"],
    DATABASES={name: {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
               for name in ("default", "secondary")},
    DATABASE_ROUTERS=[StorageRouter()],
    ANGEE_CHANGESET_MODEL="native_capture.Changeset",
    SECRET_KEY="native-capture-test",
    USE_TZ=True,
)
import django
django.setup()
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, connections
from django.db.models.deletion import ProtectedError
from angee.provenance.context import changeset
from native_capture.models import Changeset, Container, RetainedRow, Row

system_scope = nullcontext()
if sys.argv[2] == "native":
    assert "angee.base.models" not in sys.modules
    assert "angee.autoconfig" not in sys.modules
    assert "angee.provenance.models" not in sys.modules
    assert not any(name == "rebac" or name.startswith("rebac.") for name in sys.modules)
else:
    from rebac import system_context
    from angee.provenance.contrib.angee.history import HistoryMixin as AngeeHistoryMixin
    assert issubclass(Row, AngeeHistoryMixin)
    # These are explicit system-owned retention operations in the Angee host.
    system_scope = system_context(reason="test retained-reference history routing")
adapter_tables = ()
if sys.argv[2] == "angee":
    from rebac.models import PermissionAuditEvent
    adapter_tables = (PermissionAuditEvent,)
for alias in ("default", "secondary"):
    with connections[alias].schema_editor() as editor:
        for model in (ContentType, Permission, Group, User, Changeset, Container, Row, RetainedRow,
                      Row.history.model, *adapter_tables):
            editor.create_model(model)

with system_scope, changeset(fresh=True):
    if sys.argv[1] in {"failed-bulk", "protected-delete"}:
        row = Row.objects.create(value="retained")
        RetainedRow.objects.create(row=row)
        before = Changeset.objects.count()
        assert connections["default"].get_autocommit()
        with changeset(fresh=True):
            try:
                if sys.argv[1] == "failed-bulk":
                    Row.objects.bulk_create([Row(value="retained")])
                else:
                    row.delete()
            except (IntegrityError, ProtectedError):
                pass
            else:
                raise AssertionError("The captured write must fail.")
        assert Changeset.objects.count() == before
        assert Row.objects.get(pk=row.pk).value == "retained"
        assert Row.history.filter(id=row.pk).count() == 1
    elif sys.argv[1] == "explicit-source":
        row = Row(value="explicit secondary")
        row.save(using="secondary")
        assert row._state.db == "secondary"
        assert not Row.objects.using("default").exists()
        snapshot = Row.history.using("secondary").get(id=row.pk)
        assert snapshot.value == "explicit secondary"
    elif sys.argv[1] == "retained-reference":
        from angee.base.models.merge_references import ForeignKeyReference, MergePolicy

        container = Container.objects.create()
        row = Row.objects.create(container=container, value="retained pointer")
        row.delete()
        reference = ForeignKeyReference(Row, MergePolicy.MOVE, Row._meta.get_field("container"))
        assert not Row.objects.using("default").exists()
        assert Row.history.using("secondary").filter(container_id=container.pk).exists()
        assert reference.retains(container)
        settings.SIMPLE_HISTORY_ENABLED = False
        assert reference.retains(container)
    else:
        row = Row(value="independent source")
        row.save(using="default")
        before = Row.history.using("secondary").count()
        if sys.argv[1] == "bound-history":
            # An explicit historical manager binding wins over its write router;
            # the stored source still belongs to the row's default database.
            settings.DATABASE_ROUTERS[0].history_alias = "default"
            snapshots = Row.history.db_manager("secondary").bulk_history_create([row], update=True)
        else:
            snapshots = Row.history.bulk_history_create([row], update=True)
        assert len(snapshots) == 1
        assert snapshots[0]._state.db == "secondary"
        assert snapshots[0].value == "independent source"
        assert snapshots[0].history_type == "~"
        assert Row.history.using("secondary").count() == before + 1
"""
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(root / "addons"), str(tmp_path)))}
    result = subprocess.run(
        [sys.executable, "-c", script, operation, "angee" if angee_adapter else "native"],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
