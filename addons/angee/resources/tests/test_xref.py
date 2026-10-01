"""Selected resource imports authorize the existing target before changing it."""

import pytest
import yaml
from django.apps import apps
from django.core.exceptions import PermissionDenied
from rebac import actor_context, system_context

from angee.base.identity import public_id_of
from angee.resources.testing.models import Resource
from angee.testing.fixtures import composed_tables as composed_tables
from tests.conftest import (
    Page,
    Vault,
    create_platform_admin,
    create_user,
    make_addon,
    vault_for,
)
from tests.conftest import (
    addon_fixture_resources as addon_fixture_resources,
)


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.parametrize("existing_target", ["ledger", "adopted"])
def test_load_xref_authorizes_before_moving_existing_ownership(tmp_path, monkeypatch, existing_target):
    """Neither ledger reuse nor first adoption can grant its caller write access."""

    admin = create_platform_admin("resource-admin")
    with system_context(reason="resource ownership actors"):
        caller = create_user("resource-caller")
        previous_owner = create_user("resource-previous-owner")
    vault = vault_for(previous_owner)
    declaration = {"path": "target.yaml"}
    owner_field = "owner"
    target = vault
    fields = {"name": vault.name, "owner": "resource_ownership.caller"}
    if existing_target == "adopted":
        with actor_context(previous_owner):
            target = Page.objects.create_in(vault, title="Existing page")
        owner_field = "created_by"
        fields = {
            "title": target.title,
            "vault": "resource_ownership.vault",
            "created_by": "resource_ownership.caller",
        }
        declaration["adopt"] = ["vault", "title"]
    model = type(target)
    (tmp_path / "target.yaml").write_text(
        yaml.safe_dump({"_meta": {"model": model._meta.label}, "rows": [{"xref": "target", "fields": fields}]}),
        encoding="utf-8",
    )
    addon = make_addon(
        name="tests.resource_ownership", path=tmp_path, resources={"install": [declaration]},
    )
    addon.apps = apps
    addon.models = {}
    try:
        with monkeypatch.context() as patch:
            patch.setitem(apps.app_configs, addon.label, addon)
            apps.clear_cache()
            prerequisites = {"caller": caller, "vault": vault}
            if existing_target == "ledger":
                prerequisites["target"] = target
            with system_context(reason="resource ownership ledger prerequisites"):
                for xref, instance in prerequisites.items():
                    Resource.objects.create(
                        source_addon=addon.name, source_path="target.yaml", tier=Resource.Tier.INSTALL,
                        xref=xref, target_model=instance._meta.label, target_id=public_id_of(instance),
                        content_hash="previous-source",
                    )
                before_target = model.objects.values().get(pk=target.pk)
                before_ledger = list(Resource.objects.order_by("pk").values())
            assert not target.with_actor(caller).has_access("write")

            with pytest.raises(PermissionDenied):
                Resource.objects.load_xref(f"{addon.name}.target", model=model, actor=caller)

            with system_context(reason="denied resource ownership remains unchanged"):
                assert model.objects.values().get(pk=target.pk) == before_target
                assert list(Resource.objects.order_by("pk").values()) == before_ledger
                assert Vault.objects.count() == 1
                assert Page.objects.count() == (existing_target == "adopted")

            updated = Resource.objects.load_xref(f"{addon.name}.target", model=model, actor=admin)

            assert updated.pk == target.pk
            assert getattr(updated, f"{owner_field}_id") == caller.pk
            assert updated.with_actor(caller).has_access("write")
            assert Resource.objects.count() == len(before_ledger) + (existing_target == "adopted")
            assert Resource.objects.get(xref="target").target_id == public_id_of(target)
    finally:
        apps.clear_cache()


@pytest.mark.usefixtures("composed_tables")
def test_load_xref_requires_create_even_when_imported_owner_can_write(tmp_path, monkeypatch):
    """A new row's declared ownership cannot bypass its parent's create policy."""
    caller = create_user("resource-new-caller")
    previous_owner = create_user("resource-new-owner")
    vault = vault_for(previous_owner)
    (tmp_path / "target.yaml").write_text(yaml.safe_dump({
        "_meta": {"model": Page._meta.label},
        "rows": [{"xref": "target", "fields": {
            "title": "New page", "vault": "resource_create.vault", "created_by": "resource_create.caller",
        }}],
    }), encoding="utf-8")
    addon = make_addon(
        name="tests.resource_create", path=tmp_path, resources={"install": [{"path": "target.yaml"}]},
    )
    addon.apps = apps
    addon.models = {}
    try:
        with monkeypatch.context() as patch:
            patch.setitem(apps.app_configs, addon.label, addon)
            apps.clear_cache()
            with system_context(reason="resource create prerequisites"):
                for xref, instance in {"caller": caller, "vault": vault}.items():
                    Resource.objects.create(
                        source_addon=addon.name, source_path="target.yaml", tier=Resource.Tier.INSTALL,
                        xref=xref, target_model=instance._meta.label, target_id=public_id_of(instance),
                        content_hash="prerequisite",
                    )
            with pytest.raises(PermissionDenied, match="'create'"):
                Resource.objects.load_xref(f"{addon.name}.target", model=Page, actor=caller)
            with system_context(reason="denied create rolls back target and ledger"):
                assert not Page.objects.exists()
                assert not Resource.objects.filter(xref="target").exists()
    finally:
        apps.clear_cache()
