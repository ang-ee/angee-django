"""The resource ledger owns its target-model compatibility and its writes."""

from __future__ import annotations

import pytest
from django.apps import apps
from rebac import system_context

from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import ContentTypeRow, Resource
from tests.conftest import create_platform_admin
from tests.mtidemo.models import MtiChild, MtiParent


def test_ledger_row_admits_its_model_or_a_recorded_multi_table_parent() -> None:
    """A row moved onto a child keeps its parent's ledger; any other model is a collision."""

    ledger = Resource(source_addon="angee.iam", xref="seed", target_model=MtiParent._meta.label)
    ledger.require_target_model(MtiParent)
    ledger.require_target_model(MtiChild)
    with pytest.raises(ResourceLoadError, match="xref collision in angee.iam: 'seed' already targets"):
        ledger.require_target_model(ContentTypeRow)


@pytest.mark.usefixtures("composed_tables")
def test_bound_identity_stays_until_an_owner_replaces_it() -> None:
    """Binding is idempotent; another record needs ``replace``, and the ledger then names it."""

    first = create_platform_admin("ledger-first-owner")
    second = create_platform_admin("ledger-second-owner")
    iam = apps.get_app_config("iam")
    with system_context(reason="test resource ledger binding"):
        Resource.objects.bind_instance(addon=iam, xref=" owner ", instance=first, source="test")
        Resource.objects.bind_instance(addon=iam, xref="owner", instance=first, source="test")
        with pytest.raises(ResourceLoadError, match="already identifies another record"):
            Resource.objects.bind_instance(addon=iam, xref="owner", instance=second, source="test")
        assert Resource.objects.resolve("iam.owner").pk == first.pk
        Resource.objects.bind_instance(addon=iam, xref="owner", instance=second, source="test", replace=True)
        assert Resource.objects.resolve("iam.owner").pk == second.pk
        assert Resource._base_manager.filter(source_addon=iam.name, xref="owner").count() == 1
        with pytest.raises(ResourceLoadError, match="saved target"):
            Resource.objects.bind_instance(addon=iam, xref="unsaved", instance=type(first)(), source="test")
