"""Contracts for the reusable Django test composition."""

from pathlib import Path

import pytest
from django.apps import apps
from django.db import connection
from rebac import system_context

from angee.testing import models as shared_models
from angee.testing.models import Workflow
from tests.test_base_layering import _module_imports

ROOT = Path(__file__).resolve().parents[1]


def test_native_database_setup_creates_shared_model_tables(transactional_db: None) -> None:
    """Shared tables exist without requesting the permission-sync fixture."""

    tables = {
        model._meta.db_table
        for model in apps.get_models()
        if model.__module__ == shared_models.__name__
    }

    assert tables
    assert tables <= set(connection.introspection.table_names())


@pytest.mark.parametrize("case", ("first", "second"))
def test_native_database_cleanup_isolates_shared_models(transactional_db: None, case: str) -> None:
    """Each native transactional fixture starts with no prior shared-model row."""

    with system_context(reason="test native database isolation"):
        assert not Workflow.objects.filter(key="native-table-isolation").exists()
        workflow = Workflow.objects.create(key="native-table-isolation", name=f"Native isolation {case}")
        assert Workflow.objects.get(pk=workflow.pk).name == f"Native isolation {case}"


def test_production_sources_do_not_import_test_support() -> None:
    """The opt-in testing composition never becomes a serving dependency."""

    violations = {}
    for root in (ROOT / "angee", ROOT / "addons"):
        for path in sorted(root.rglob("*.py")):
            if (
                path.is_relative_to(ROOT / "angee" / "testing")
                or "tests" in path.relative_to(ROOT).parts
                or path.name in {"conftest.py", "tests.py"}
                or path.name.startswith(("test_", "tests_"))
            ):
                continue
            forbidden = sorted(
                name
                for name in _module_imports(path)
                if name in {"tests", "angee.testing"} or name.startswith(("tests.", "angee.testing."))
            )
            if forbidden:
                violations[str(path.relative_to(ROOT))] = forbidden

    assert not violations
