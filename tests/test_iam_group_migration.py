"""Historical adoption coverage for IAM-owned groups."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth.models import Group as DjangoGroup
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.db.migrations.state import ProjectState

from angee.iam.runtime_migrations.adopt_auth_groups import (
    Migration,
    applies,
    copy_auth_groups,
)
from tests.iam_models import Group


def test_group_adoption_covers_absent_and_proxy_iam_state() -> None:
    """The cutover covers hosts from before and after the temporary proxy."""

    state = ProjectState.from_apps(apps)
    assert not applies(state)
    without_owner = state.clone()
    without_owner.remove_model("iam", "user")
    without_owner.remove_model("iam", "group")
    assert not applies(without_owner)
    without_iam = state.clone()
    without_iam.remove_model("iam", "group")
    assert applies(without_iam)
    proxy = state.clone()
    proxy.models[("iam", "group")].options["proxy"] = True
    assert applies(proxy)
    without_source = proxy.clone()
    without_source.remove_model("auth", "group")
    assert not applies(without_source)
    unexpected = state.clone()
    unexpected.models[("iam", "group")].fields["description"] = models.CharField(max_length=10)
    with pytest.raises(ImproperlyConfigured, match="unexpected concrete IAM Group state"):
        applies(unexpected)


def test_group_adoption_state_operation_builds_concrete_model_from_both_histories() -> None:
    """The same runtime operation replaces a proxy or creates an absent IAM model."""

    current = ProjectState.from_apps(apps)
    absent = current.clone()
    absent.remove_model("iam", "group")
    proxy = current.clone()
    proxy.models[("iam", "group")].options["proxy"] = True

    for historical in (absent, proxy):
        migrated = Migration("probe", "iam").mutate_state(historical)
        group = migrated.models[("iam", "group")]
        assert not group.options.get("proxy", False)
        assert group.fields["name"].unique
        assert "sqid" not in group.fields


@pytest.mark.django_db(transaction=True)
def test_group_adoption_preserves_primary_keys_and_resets_sequence() -> None:
    """Historical auth groups retain PKs and new IAM rows continue above them."""

    source = DjangoGroup.objects.create(name="Historical reviewers")
    copy_auth_groups(apps, SimpleNamespace(connection=connection))

    adopted = Group._base_manager.get(pk=source.pk)
    assert adopted.name == source.name
    assert adopted.description == ""
    assert adopted.sqid.startswith("grp_")

    copy_auth_groups(apps, SimpleNamespace(connection=connection))
    created = Group._base_manager.create(name="New reviewers")
    assert created.pk > source.pk
