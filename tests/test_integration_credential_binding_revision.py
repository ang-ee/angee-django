"""Integration credential-binding generation and write-owner regressions."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection, connections, models
from django.db.migrations.state import ModelState, ProjectState
from django.db.models import F
from rebac import actor_context, system_context

from angee.integrate.credentials import CredentialKind
from tests.conftest import (
    Credential,
    ExternalAccount,
    Integration,
    OAuthClient,
    VcsBridge,
    Vendor,
    _clear_model_tables,
    _create_missing_tables,
)

_MODELS = (OAuthClient, ExternalAccount, Credential, Vendor, Integration, VcsBridge)


@pytest.fixture()
def integration_binding_tables(transactional_db: Any) -> Iterator[None]:
    """Create the concrete connection and Integration parent/child tables."""

    del transactional_db
    created = _create_missing_tables(_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(_MODELS)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def _binding_rows(label: str) -> tuple[Any, Any, Any, Any]:
    """Create an owner, vendor, and two credentials for one binding test."""

    user = get_user_model().objects.create_user(username=f"binding-{label}")
    with system_context(reason="integration credential binding setup"):
        client = OAuthClient.objects.create(
            slug=f"binding-{label}",
            display_name=f"Binding {label}",
            client_id=f"binding-{label}",
        )
        first = Credential.objects.upsert_for_user(
            user,
            client,
            CredentialKind.STATIC_TOKEN,
            {"api_key": "first"},
        )
        second = Credential.objects.create_local_credential(
            user,
            kind=CredentialKind.STATIC_TOKEN,
            name=f"binding-{label}-second",
            material={"api_key": "second"},
        )
        vendor = Vendor.objects.create(slug=f"binding-{label}", display_name=f"Binding {label}")
    return user, vendor, first, second


@pytest.mark.django_db(transaction=True)
def test_credential_binding_revision_tracks_only_real_identity_changes(
    integration_binding_tables: None,
) -> None:
    """New rows start at one; no-ops stay put and A→B→A advances twice."""

    user, vendor, first, second = _binding_rows("identity")
    with system_context(reason="integration credential binding create"):
        integration = VcsBridge.objects.create(
            owner=user,
            vendor=vendor,
            credential=first,
            backend_class="local",
        )
    assert integration.credential_binding_revision == 1

    current = Integration.objects.with_actor(user).get(pk=integration.pk)
    current.credential = first
    current.save(update_fields=["credential", "updated_at"])
    assert current.credential_binding_revision == 1

    current.credential = second
    current.save(update_fields=["credential", "updated_at"])
    assert current.credential_binding_revision == 2
    current.credential = first
    current.save(update_fields=["credential", "updated_at"])
    assert current.credential_binding_revision == 3
    bridge = VcsBridge.objects.with_actor(user).get(pk=integration.pk)
    assert bridge.sync_admission_generation() == "3"


@pytest.mark.django_db(transaction=True)
def test_binding_change_requires_actor_and_rejects_stale_full_save(
    integration_binding_tables: None,
) -> None:
    """Ambient system scope cannot rebind, and a stale full save cannot undo a winner."""

    user, vendor, first, second = _binding_rows("stale")
    with system_context(reason="integration credential binding create"):
        integration = Integration.objects.create(owner=user, vendor=vendor, credential=first)
        unscoped = Integration.objects.get(pk=integration.pk)
    unscoped.credential = second
    with system_context(reason="integration credential binding forbidden system write"):
        with pytest.raises(TypeError, match="authorized actor"):
            unscoped.save(update_fields=["credential", "updated_at"])

    stale = Integration.objects.with_actor(user).get(pk=integration.pk)
    winner = Integration.objects.with_actor(user).get(pk=integration.pk)
    winner.credential = second
    winner.save(update_fields=["credential", "updated_at"])
    stale.display_name = "stale overwrite"
    with pytest.raises(TypeError, match="revision is stale"):
        stale.save()
    integration.refresh_from_db()
    assert integration.credential_id == second.pk
    assert integration.credential_binding_revision == 2


@pytest.mark.django_db(transaction=True)
def test_metadata_full_save_does_not_require_binding_authority(
    integration_binding_tables: None,
) -> None:
    """A full save with the canonical binding remains an ordinary metadata write."""

    user, vendor, first, _second = _binding_rows("metadata")
    with system_context(reason="integration credential binding create"):
        integration = Integration.objects.create(owner=user, vendor=vendor, credential=first)
        integration.display_name = "Updated by its metadata owner"
        integration.save()
        integration.refresh_from_db()
    assert integration.display_name == "Updated by its metadata owner"
    assert integration.credential_binding_revision == 1


@pytest.mark.django_db(transaction=True)
def test_constructed_existing_and_absent_rows_preserve_binding_identity(
    integration_binding_tables: None,
) -> None:
    """An adding-state instance cannot bypass an existing row or forge a new revision."""

    user, vendor, first, second = _binding_rows("constructed")
    with system_context(reason="integration credential constructed setup"):
        persisted = Integration.objects.create(owner=user, vendor=vendor, credential=first)

    constructed = Integration(
        pk=persisted.pk,
        owner=user,
        vendor=vendor,
        credential=second,
        credential_binding_revision=1,
    )
    with actor_context(user):
        constructed.save(update_fields=["credential", "updated_at"])
    persisted.refresh_from_db()
    assert persisted.credential_id == second.pk
    assert persisted.credential_binding_revision == 2

    absent_pk = int(persisted.pk) + 10_000
    inserted = Integration(
        pk=absent_pk,
        owner=user,
        vendor=vendor,
        credential=first,
        credential_binding_revision=1,
    )
    with system_context(reason="integration credential explicit-pk insert"):
        inserted.save()
    inserted.refresh_from_db()
    assert inserted.credential_id == first.pk
    assert inserted.credential_binding_revision == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL Integration row-lock behavior")
def test_concurrent_binding_writers_serialize_one_revision(
    integration_binding_tables: None,
) -> None:
    """Two stale writers cannot both claim the same credential-binding generation."""

    user, vendor, first, second = _binding_rows("race")
    with system_context(reason="integration credential race setup"):
        integration = Integration.objects.create(owner=user, vendor=vendor, credential=first)

    import threading

    barrier = threading.Barrier(2)

    def write() -> str:
        connections.close_all()
        try:
            with system_context(reason="integration credential race user"):
                worker_user = get_user_model().objects.get(pk=user.pk)
            row = Integration.objects.with_actor(worker_user).get(pk=integration.pk)
            row.credential_id = second.pk
            barrier.wait(timeout=10)
            try:
                row.save(update_fields=["credential", "updated_at"])
            except TypeError as error:
                assert "revision is stale" in str(error)
                return "stale"
            return "saved"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=15) for future in [executor.submit(write), executor.submit(write)]]
    assert sorted(results) == ["saved", "stale"]
    integration.refresh_from_db()
    assert integration.credential_id == second.pk
    assert integration.credential_binding_revision == 2


@pytest.mark.django_db(transaction=True)
def test_parent_save_invokes_concrete_child_binding_policy(
    integration_binding_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addressing Integration cannot skip the concrete capability's binding law."""

    user, vendor, first, second = _binding_rows("child")
    with system_context(reason="integration credential child create"):
        bridge = VcsBridge.objects.create(
            owner=user,
            vendor=vendor,
            credential=first,
            backend_class="local",
        )

    def deny(self: Any, **kwargs: Any) -> None:
        del self, kwargs
        raise TypeError("concrete child denied credential rebinding")

    monkeypatch.setattr(VcsBridge, "validate_integration_credential_binding_change", deny)
    parent = Integration.objects.with_actor(user).get(pk=bridge.pk)
    parent.credential = second
    with pytest.raises(TypeError, match="concrete child denied"):
        parent.save(update_fields=["credential", "updated_at"])
    parent.refresh_from_db()
    assert parent.credential_id == first.pk
    assert parent.credential_binding_revision == 1


@pytest.mark.django_db(transaction=True)
def test_queryset_bulk_and_forged_revision_paths_fail_closed(
    integration_binding_tables: None,
) -> None:
    """Parent, system, field-object, and bulk writes cannot forge the binding fact."""

    user, vendor, first, second = _binding_rows("bulk")
    with system_context(reason="integration credential binding create"):
        integration = Integration.objects.create(owner=user, vendor=vendor, credential=first)
    with pytest.raises(TypeError, match="Integration.save"):
        Integration.objects.with_actor(user).filter(pk=integration.pk).update(credential=second)
    with pytest.raises(TypeError, match="Integration.save"):
        Integration.system_queryset().filter(pk=integration.pk).update(credential_id=second.pk)
    with pytest.raises(TypeError, match="Integration.save"):
        Integration.objects.with_actor(user).filter(pk=integration.pk).update(
            credential_binding_revision=F("credential_binding_revision") + 1
        )

    integration.credential_binding_revision = 2
    with pytest.raises(TypeError, match="Integration.save"):
        Integration.objects.bulk_update(
            [integration],
            [Integration._meta.get_field("credential_binding_revision")],
        )
    forged = Integration(owner=user, vendor=vendor, credential=first, credential_binding_revision=2)
    with pytest.raises(TypeError, match="revision 1"):
        Integration.objects.bulk_create([forged])


def _integration_state(field: models.Field | None) -> ProjectState:
    """Return a minimal historical Integration state for migration guards."""

    fields: list[tuple[str, models.Field]] = [("id", models.AutoField(primary_key=True))]
    if field is not None:
        fields.append(("credential_binding_revision", field))
    state = ProjectState()
    state.add_model(ModelState("integrate", "Integration", fields))
    return state


def test_integration_credential_binding_revision_migration_is_guarded() -> None:
    """The append migration accepts only absent or exact current field shape."""

    module = importlib.import_module(
        "angee.integrate.runtime_migrations.integration_credential_binding_revision"
    )
    missing = _integration_state(None)
    current = _integration_state(models.PositiveBigIntegerField(default=1, editable=False))
    wrong = _integration_state(models.PositiveIntegerField(default=2))
    nullable = _integration_state(
        models.PositiveBigIntegerField(default=1, editable=False, null=True)
    )

    assert module.applies(ProjectState()) is False
    assert module.applies(missing) is True
    assert module.applies(current) is False
    with pytest.raises(ImproperlyConfigured, match="partial Integration credential-binding"):
        module.applies(wrong)
    with pytest.raises(ImproperlyConfigured, match="partial Integration credential-binding"):
        module.applies(nullable)

    migrated = module.Migration("probe", "integrate").mutate_state(missing)
    field = migrated.models["integrate", "integration"].fields["credential_binding_revision"]
    assert isinstance(field, models.PositiveBigIntegerField)
    assert field.default == 1
    assert field.editable is False
