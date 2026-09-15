"""Credential material generation and write-owner regressions."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, connections, models
from django.db.migrations.state import ModelState, ProjectState
from django.db.models import F
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.oauth.client import OAuthClientProtocol
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    Credential,
    OAuthClient,
    _clear_model_tables,
    _create_missing_tables,
)


@pytest.fixture()
def credential_tables(transactional_db: Any) -> Iterator[None]:
    """Provide the concrete source-test credential tables without leaking rows."""

    created = _create_missing_tables()
    try:
        yield
    finally:
        _clear_model_tables(IAM_CONNECTION_TEST_MODELS)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def _user_and_client(label: str) -> tuple[Any, Any]:
    """Return one credential owner and provider identity."""

    user = get_user_model().objects.create_user(
        username=f"credential-revision-{label}",
        email=f"{label}@example.test",
    )
    with system_context(reason="credential material revision test setup"):
        client = OAuthClient.objects.create(
            slug=f"revision-{label}",
            display_name=f"Revision {label}",
            client_id=f"revision-{label}-client",
        )
    return user, client


def _run_two_writers(write: Any) -> list[Any]:
    """Race two PostgreSQL writers on independent thread-local connections."""

    import threading

    barrier = threading.Barrier(2)

    def run(index: int) -> Any:
        connections.close_all()
        try:
            return write(index, barrier)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        return [future.result(timeout=15) for future in [executor.submit(run, 0), executor.submit(run, 1)]]


@pytest.mark.django_db(transaction=True)
def test_material_owner_revisions_only_actual_canonical_changes(credential_tables: None) -> None:
    """Create starts at one; no-ops stay put and A→B→A remains distinguishable."""

    user, _client = _user_and_client("local")
    credential = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-local",
        material={"api_key": "A"},
    )
    assert credential.material_revision == 1

    same = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-local",
        material={"api_key": "A"},
    )
    assert same.pk == credential.pk
    assert same.material_revision == 1

    changed = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-local",
        material={"api_key": "B"},
    )
    assert changed.material_revision == 2
    returned = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-local",
        material={"api_key": "A"},
    )
    assert returned.material_revision == 3
    assert returned.reveal() == {"api_key": "A"}


@pytest.mark.django_db(transaction=True)
def test_material_revision_uses_canonical_json_identity(credential_tables: None) -> None:
    """Key order is immaterial, while JSON booleans and numbers stay distinct."""

    user, _client = _user_and_client("json")
    credential = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-json",
        material={"flag": True, "api_key": "A"},
    )
    assert credential.material_revision == 1
    same = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-json",
        material={"api_key": "A", "flag": True},
    )
    assert same.material_revision == 1

    integer = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-json",
        material={"api_key": "A", "flag": 1},
    )
    assert integer.material_revision == 2
    decimal = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-json",
        material={"api_key": "A", "flag": 1.0},
    )
    assert decimal.material_revision == 3


@pytest.mark.django_db(transaction=True)
def test_update_material_merges_stale_writers_and_revisions_transient_keys(
    credential_tables: None,
) -> None:
    """The existing locked merge owner bumps once per real add/change/delete."""

    user, _client = _user_and_client("merge")
    first = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.BASIC_AUTH,
        name="revision-merge",
        material={"username": "alice", "password": "one"},
    )
    second = Credential.objects.sudo(reason="credential revision stale writer").get(pk=first.pk)

    first.update_material(password="two")
    assert first.material_revision == 2
    second.update_material(transient="answer")
    assert second.material_revision == 3
    second.update_material(transient="answer")
    assert second.material_revision == 3
    first.update_material(missing=None)
    assert first.material_revision == 3
    first.update_material(transient=None)
    assert first.material_revision == 4

    fresh = Credential.objects.sudo(reason="credential revision merge verify").get(pk=first.pk)
    assert fresh.reveal() == {"password": "two", "username": "alice"}
    assert fresh.material_revision == 4


@pytest.mark.django_db(transaction=True)
def test_kind_change_uses_the_same_validated_material_owner(credential_tables: None) -> None:
    """A repeated local identity may change kind only through the validating factory."""

    user, _client = _user_and_client("kind")
    credential = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-kind",
        material={"api_key": "token"},
    )
    changed = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.BASIC_AUTH,
        name="revision-kind",
        material={"username": "alice", "password": "secret"},
    )
    assert changed.pk == credential.pk
    assert changed.kind == CredentialKind.BASIC_AUTH
    assert changed.material_revision == 2


@pytest.mark.django_db(transaction=True)
def test_oauth_refresh_advances_the_same_material_revision(
    credential_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider refresh reuses the manager owner and fences rotated tokens."""

    user, client = _user_and_client("oauth")
    client.supports_refresh = True
    client.token_endpoint = "https://provider.example.test/token"
    with system_context(reason="credential revision oauth client setup"):
        client.save(update_fields=["supports_refresh", "token_endpoint", "updated_at"])
    credential = Credential.objects.upsert_for_user(
        user,
        client,
        CredentialKind.OAUTH,
        {"access_token": "A", "refresh_token": "R-A", "expires_in": 3600},
    )

    def refresh_token(self: Any, *, refresh_token: str) -> dict[str, Any]:
        assert refresh_token == "R-A"
        return {"access_token": "B", "refresh_token": "R-B", "expires_in": 7200}

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", refresh_token)
    with system_context(reason="credential revision oauth refresh"):
        credential.refresh_now()
    credential.refresh_from_db()
    assert credential.material_revision == 2
    assert credential.reveal()["refresh_token"] == "R-B"

    same = Credential.objects.upsert_for_user(
        user,
        client,
        CredentialKind.OAUTH,
        credential.reveal(),
    )
    assert same.material_revision == 2


@pytest.mark.django_db(transaction=True)
def test_metadata_update_fields_does_not_overwrite_stale_material(credential_tables: None) -> None:
    """Health metadata stays writable, while a stale full save fails closed."""

    user, _client = _user_and_client("metadata")
    current = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-metadata",
        material={"api_key": "old"},
    )
    stale = Credential.objects.sudo(reason="credential revision stale metadata").get(pk=current.pk)
    current.replace_material({"api_key": "new"})

    stale.status = "revoked"
    with system_context(reason="credential revision metadata update"):
        stale.save(update_fields=["status", "updated_at"])
    fresh = Credential.objects.sudo(reason="credential revision metadata verify").get(pk=current.pk)
    assert fresh.status == "revoked"
    assert fresh.reveal() == {"api_key": "new"}
    assert fresh.material_revision == 2

    stale.name = "stale full save"
    with pytest.raises(TypeError, match="material owner"):
        stale.save()


@pytest.mark.django_db(transaction=True)
def test_instance_and_constructed_existing_row_cannot_forge_material(
    credential_tables: None,
) -> None:
    """Persisted existence, rather than `_state.adding`, controls the guard."""

    user, client = _user_and_client("instance")
    credential = Credential.objects.upsert_for_user(
        user,
        client,
        CredentialKind.STATIC_TOKEN,
        {"api_key": "kept"},
    )
    credential.material = Credential.encode_material({"api_key": "forged"})
    with pytest.raises(TypeError, match="material owner"):
        credential.save(update_fields=["material", "updated_at"])

    constructed = Credential(
        pk=credential.pk,
        user=user,
        oauth_client=client,
        name=credential.name,
        kind=CredentialKind.STATIC_TOKEN,
        material=Credential.encode_material({"api_key": "constructed"}),
        material_revision=1,
    )
    with pytest.raises(TypeError, match="material owner"):
        constructed.save()

    credential.refresh_from_db()
    assert credential.reveal() == {"api_key": "kept"}
    assert credential.material_revision == 1


@pytest.mark.django_db(transaction=True)
def test_new_rows_require_revision_one_and_explicit_pk_is_an_insert(
    credential_tables: None,
) -> None:
    """Explicit-PK creation is insert-only and update-only requests stay updates."""

    user, _client = _user_and_client("insert")
    forged = Credential(
        user=user,
        name="forged-revision",
        kind=CredentialKind.STATIC_TOKEN,
        material=Credential.encode_material({"api_key": "x"}),
        material_revision=2,
    )
    with pytest.raises(TypeError, match="revision 1"):
        forged.save()

    explicit = Credential(
        pk=987654321,
        user=user,
        name="explicit-revision",
        kind=CredentialKind.STATIC_TOKEN,
        material=Credential.encode_material({"api_key": "x"}),
    )
    with system_context(reason="credential explicit insert"):
        explicit.save()
    persisted = Credential.objects.sudo(reason="credential explicit insert verify").get(
        pk=explicit.pk
    )
    assert persisted.material_revision == 1

    missing = Credential(
        pk=987654322,
        user=user,
        name="missing-update",
        kind=CredentialKind.STATIC_TOKEN,
        material=Credential.encode_material({"api_key": "x"}),
    )
    with system_context(reason="credential absent explicit update"), pytest.raises(
        Credential.NotUpdated
    ):
        missing.save(force_update=True)
    with system_context(reason="credential absent update-fields"), pytest.raises(
        Credential.NotUpdated
    ):
        missing.save(update_fields=["material"])


@pytest.mark.django_db(transaction=True)
def test_false_absence_never_overwrites_an_explicit_pk_winner(
    credential_tables: None,
) -> None:
    """A stale absence observation forces INSERT, preserving a concurrent winner."""

    user, _client = _user_and_client("insert-race")
    winner = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-insert-race",
        material={"api_key": "winner"},
    )
    replacement = Credential(
        pk=winner.pk,
        user=user,
        name="replacement",
        kind=CredentialKind.STATIC_TOKEN,
        material=Credential.encode_material({"api_key": "loser"}),
    )
    queryset_type = type(Credential.objects.all())
    with (
        patch.object(queryset_type, "first", autospec=True, return_value=None),
        system_context(reason="credential stale absence race"),
        pytest.raises(IntegrityError),
    ):
        replacement.save()

    winner.refresh_from_db()
    assert winner.name == "revision-insert-race"
    assert winner.reveal() == {"api_key": "winner"}
    assert winner.material_revision == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL credential row-lock behavior")
@pytest.mark.parametrize(
    ("material_values", "expected_revision"),
    [(["same", "same"], 1), (["first", "second"], 2)],
)
def test_concurrent_first_upsert_serializes_material_revision(
    credential_tables: None,
    material_values: list[str],
    expected_revision: int,
) -> None:
    """A same-identity creation race retains one row and every real change."""

    user, _client = _user_and_client(f"create-{expected_revision}")

    def write(index: int, barrier: Any) -> int:
        with system_context(reason="credential create race user"):
            worker_user = get_user_model().objects.get(pk=user.pk)
        barrier.wait(timeout=10)
        credential = Credential.objects.create_local_credential(
            worker_user,
            kind=CredentialKind.STATIC_TOKEN,
            name=f"revision-create-{expected_revision}",
            material={"api_key": material_values[index]},
        )
        return int(credential.material_revision)

    revisions = _run_two_writers(write)
    credential = Credential.objects.sudo(reason="credential create race verify").get(
        user=user,
        name=f"revision-create-{expected_revision}",
    )
    assert Credential.objects.sudo(reason="credential create race count").filter(
        user=user,
        name=f"revision-create-{expected_revision}",
    ).count() == 1
    assert credential.material_revision == expected_revision
    assert max(revisions) == expected_revision
    assert credential.reveal()["api_key"] in material_values


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL credential row-lock behavior")
def test_concurrent_stale_material_merges_preserve_both_changes(
    credential_tables: None,
) -> None:
    """Stale instances re-read under lock, preserving both changes and generations."""

    user, _client = _user_and_client("merge-race")
    credential = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.BASIC_AUTH,
        name="revision-merge-race",
        material={"username": "alice", "password": "initial"},
    )

    def write(index: int, barrier: Any) -> int:
        stale = Credential.objects.sudo(reason="credential merge race load").get(pk=credential.pk)
        barrier.wait(timeout=10)
        stale.update_material(**{f"worker_{index}": index})
        return int(stale.material_revision)

    revisions = _run_two_writers(write)
    credential.refresh_from_db()
    assert credential.reveal() == {
        "password": "initial",
        "username": "alice",
        "worker_0": 0,
        "worker_1": 1,
    }
    assert credential.material_revision == 3
    assert all(2 <= revision <= 3 for revision in revisions)
    assert max(revisions) == 3


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL credential row-lock behavior")
def test_concurrent_identical_replacements_are_revision_noops(
    credential_tables: None,
) -> None:
    """Two identical retained-row writes do not manufacture generations."""

    user, _client = _user_and_client("noop-race")
    credential = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-noop-race",
        material={"api_key": "same"},
    )

    def write(_index: int, barrier: Any) -> int:
        with system_context(reason="credential noop race user"):
            worker_user = get_user_model().objects.get(pk=user.pk)
        barrier.wait(timeout=10)
        row = Credential.objects.create_local_credential(
            worker_user,
            kind=CredentialKind.STATIC_TOKEN,
            name="revision-noop-race",
            material={"api_key": "same"},
        )
        return int(row.material_revision)

    revisions = _run_two_writers(write)
    credential.refresh_from_db()
    assert revisions == [1, 1]
    assert credential.material_revision == 1


@pytest.mark.django_db(transaction=True)
def test_queryset_and_system_queryset_cannot_bypass_material_owner(
    credential_tables: None,
) -> None:
    """Direct SQL and expression writes cannot forge material generations."""

    user, _client = _user_and_client("queryset")
    credential = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-queryset",
        material={"api_key": "x"},
    )
    encoded = Credential.encode_material({"api_key": "y"})
    with pytest.raises(TypeError, match="material owner"):
        Credential.objects.filter(pk=credential.pk).update(material=encoded)
    with pytest.raises(TypeError, match="material owner"):
        Credential.system_queryset().filter(pk=credential.pk).update(material_revision=F("material_revision") + 1)
    with pytest.raises(TypeError, match="material owner"):
        Credential.objects.update_or_create(
            pk=credential.pk,
            defaults={"material": encoded},
        )
    with pytest.raises(TypeError, match="material owner"):
        Credential.objects.update_or_create(
            name="generic-material-upsert",
            defaults={"status": "active"},
            create_defaults={
                "user": user,
                "kind": CredentialKind.STATIC_TOKEN,
                "material": encoded,
            },
        )


@pytest.mark.django_db(transaction=True)
def test_bulk_paths_reject_material_updates_and_forged_new_revisions(
    credential_tables: None,
) -> None:
    """Bulk and conflict paths cannot bypass the per-row locked owner."""

    user, _client = _user_and_client("bulk")
    credential = Credential.objects.create_local_credential(
        user,
        kind=CredentialKind.STATIC_TOKEN,
        name="revision-bulk",
        material={"api_key": "x"},
    )
    credential.material_revision = 2
    with pytest.raises(TypeError, match="material owner"):
        Credential.objects.bulk_update([credential], ["material_revision"])

    conflict = Credential(
        user=user,
        name="revision-bulk",
        kind=CredentialKind.STATIC_TOKEN,
        material=Credential.encode_material({"api_key": "y"}),
    )
    with pytest.raises(TypeError, match="material owner"):
        Credential.objects.bulk_create(
            [conflict],
            update_conflicts=True,
            update_fields=["material"],
            unique_fields=["user", "name"],
        )

    forged = Credential(
        user=user,
        name="revision-bulk-forged",
        kind=CredentialKind.STATIC_TOKEN,
        material=Credential.encode_material({"api_key": "z"}),
        material_revision=3,
    )
    with pytest.raises(TypeError, match="revision 1"):
        Credential.objects.bulk_create([forged])


def _credential_state(field: models.Field | None) -> ProjectState:
    """Return a minimal historical Credential state for migration guards."""

    fields: list[tuple[str, models.Field]] = [("id", models.AutoField(primary_key=True))]
    if field is not None:
        fields.append(("material_revision", field))
    state = ProjectState()
    state.add_model(ModelState("integrate", "Credential", fields))
    return state


def test_credential_material_revision_migration_is_append_only_and_guarded() -> None:
    """The runtime migration adds only the exact missing generation field."""

    module = importlib.import_module(
        "angee.integrate.runtime_migrations.credential_material_revision"
    )
    missing = _credential_state(None)
    current = _credential_state(
        models.PositiveBigIntegerField(default=1, editable=False)
    )
    wrong = _credential_state(models.PositiveIntegerField(default=2))
    nullable = _credential_state(
        models.PositiveBigIntegerField(default=1, editable=False, null=True)
    )

    assert module.applies(ProjectState()) is False
    assert module.applies(missing) is True
    assert module.applies(current) is False
    with pytest.raises(ImproperlyConfigured, match="partial Credential material revision"):
        module.applies(wrong)
    with pytest.raises(ImproperlyConfigured, match="partial Credential material revision"):
        module.applies(nullable)

    migrated = module.Migration("probe", "integrate").mutate_state(missing)
    field = migrated.models["integrate", "credential"].fields["material_revision"]
    assert isinstance(field, models.PositiveBigIntegerField)
    assert field.default == 1
    assert field.editable is False
