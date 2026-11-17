"""Create-only seed fields reach only a row the load creates, never an adopted or re-applied one."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from rebac import system_context

from angee.resources.entries import ResourceEntry
from angee.resources.exceptions import ResourceLoadError
from angee.resources.loader import AngeeResource
from angee.resources.testing.models import Resource
from tests.conftest import OAuthClient, make_addon

pytestmark = pytest.mark.django_db

SEED = "010_integrate.oauthclient.yaml"
POLICY = "020_integrate.oauthclient.yaml"
IDENTITY = {"slug": "create-only", "environment": "prod"}


def _row(xref: str, **fields: Any) -> str:
    values = {**IDENTITY, **fields}
    return f"- xref: {xref}\n" + "".join(f"  {name}: {value}\n" for name, value in values.items())


def _addon(tmp_path: Path, files: dict[str, str], entries: list[dict[str, Any]]) -> Any:
    (tmp_path / "resources").mkdir(exist_ok=True)
    for name, body in files.items():
        (tmp_path / "resources" / name).write_text(body)
    return make_addon(path=tmp_path, resources={"install": entries})


def _seed_entry(**declaration: Any) -> dict[str, Any]:
    return {"path": SEED, "adopt": ["slug", "environment"], **declaration}


def _load(addon: Any) -> Any:
    return Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])


def _client() -> Any:
    with system_context(reason="test create-only row"):
        return OAuthClient.objects.get(**IDENTITY)


def test_create_only_fields_reach_the_row_the_load_creates_and_stay_out_of_the_hash(tmp_path: Path) -> None:
    files = {SEED: _row("client", display_name="Seeded", is_enabled="false")}
    addon = _addon(tmp_path, files, [_seed_entry(create_only=["is_enabled"])])

    _load(addon)
    assert (_client().display_name, _client().is_enabled) == ("Seeded", False)

    with system_context(reason="test operator edit"):
        OAuthClient.objects.filter(**IDENTITY).update(is_enabled=True)
    # A changed create-only value alone is not a changed row.
    (tmp_path / "resources" / SEED).write_text(_row("client", display_name="Seeded", is_enabled="true"))
    assert _load(addon).updated == 0
    # A re-applied row takes its other fields, never the create-only ones.
    (tmp_path / "resources" / SEED).write_text(_row("client", display_name="Renamed", is_enabled="false"))
    assert _load(addon).updated == 1
    assert (_client().display_name, _client().is_enabled) == ("Renamed", True)


def test_create_only_fields_never_reach_an_adopted_row(tmp_path: Path) -> None:
    with system_context(reason="test operator row"):
        OAuthClient.objects.create(**IDENTITY, display_name="Operator", is_enabled=True)
    files = {SEED: _row("client", display_name="Seeded", is_enabled="false")}

    _load(_addon(tmp_path, files, [_seed_entry(create_only=["is_enabled"])]))

    assert (_client().display_name, _client().is_enabled) == ("Seeded", True)


@pytest.mark.parametrize("existing", [False, True])
def test_a_row_another_entry_creates_in_the_same_load_counts_as_created(tmp_path: Path, existing: bool) -> None:
    if existing:
        with system_context(reason="test operator row"):
            OAuthClient.objects.create(**IDENTITY, display_name="Operator", is_enabled=True)
    files = {SEED: _row("client", display_name="Seeded"), POLICY: _row("client_policy", is_enabled="false")}
    entries = [
        _seed_entry(),
        {"path": POLICY, "adopt": ["slug", "environment"], "create_only": ["is_enabled"], "depends_on": SEED},
    ]

    _load(_addon(tmp_path, files, entries))

    assert _client().is_enabled is existing


def test_a_row_an_owner_verb_persists_counts_as_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native_save = AngeeResource.save_instance

    def verb_save(resource: Any, instance: Any, is_create: bool, row: Any, **kwargs: Any) -> None:
        """Persist a new row as an owner verb does: a separate saved object the loader adopts."""

        if not is_create:
            native_save(resource, instance, is_create, row, **kwargs)
            return
        resource.before_save_instance(instance, row, **kwargs)
        values = {field.attname: getattr(instance, field.attname) for field in instance._meta.concrete_fields}
        values.pop(instance._meta.pk.attname)
        resource.adopt_persisted_instance(instance, type(instance).objects.create(**values), row, **kwargs)

    monkeypatch.setattr(AngeeResource, "save_instance", verb_save)
    files = {SEED: _row("client", display_name="Seeded"), POLICY: _row("client_policy", is_enabled="false")}
    entries = [
        _seed_entry(),
        {"path": POLICY, "adopt": ["slug", "environment"], "create_only": ["is_enabled"], "depends_on": SEED},
    ]

    _load(_addon(tmp_path, files, entries))

    assert _client().is_enabled is False


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        ({"adopt": ["slug", "environment"], "create_only": ["slug"]}, "adoption field"),
        ({"kind": "grants", "create_only": ["subject"]}, "row entries"),
    ],
)
def test_create_only_is_refused_on_adoption_fields_and_grant_entries(
    declaration: dict[str, Any], message: str,
) -> None:
    with pytest.raises(ImproperlyConfigured, match=message):
        ResourceEntry.from_declaration(apps.get_app_config("integrate"), "install", {"path": SEED, **declaration})


def test_an_unknown_create_only_field_is_refused(tmp_path: Path) -> None:
    files = {SEED: _row("client", display_name="Seeded")}

    with pytest.raises(ResourceLoadError, match="unknown create_only field"):
        _load(_addon(tmp_path, files, [_seed_entry(create_only=["no_such_field"])]))
