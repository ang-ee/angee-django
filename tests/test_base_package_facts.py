"""Capability tables use one packaged-TOML reader and no disabled imports."""
from __future__ import annotations

import importlib
import json
from importlib import metadata
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django_compose.components import enabled_apps, installed_components

from angee.base import addons
from angee.base.addons import addon_facts, package_document
from angee.graphql.schema import GraphQLSchemas


def _distribution(tmp_path: Path, document: str, *, editable: bool = False) -> metadata.PathDistribution:
    info = tmp_path / 'acme_invoice-1.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: acme-invoice\nVersion: 1\n')
    (info / 'entry_points.txt').write_text('[angee.addons]\nacme.invoice = acme.invoice.apps.InvoiceConfig\n')
    if editable:
        (tmp_path / 'pyproject.toml').write_text(document)
        (info / 'direct_url.json').write_text(json.dumps({'url': tmp_path.as_uri(), 'dir_info': {'editable': True}}))
    else:
        package = tmp_path / 'acme/invoice'
        package.mkdir(parents=True)
        (package / 'pyproject.toml').write_text(document)
        (info / 'RECORD').write_text('acme/invoice/pyproject.toml,,\n')
    return metadata.PathDistribution(info)


@pytest.mark.parametrize('editable', [False, True])
def test_read_disabled_third_party_capability_from_native_record_or_editable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editable: bool,
) -> None:
    _distribution(tmp_path, '''
[tool.acme.billing]
currency = "EUR"
[tool.angee.platform]
category = "invoicing"
''', editable=editable)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(importlib, 'import_module', lambda *args, **kwargs: pytest.fail('disabled addon imported'))
    assert addon_facts('acme.invoice', 'acme.billing') == {'currency': 'EUR'}
    assert addon_facts('acme.invoice', 'angee.platform') == {'category': 'invoicing'}
    assert addon_facts('acme.invoice', 'angee.resources') == {}
    assert addon_facts('django.contrib.auth', 'angee.resources') == {}


def test_malformed_capability_is_rejected_at_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _distribution(tmp_path, '[tool.acme]\nbilling = "wrong"\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ImproperlyConfigured, match='tool.acme.billing must be a TOML table'):
        addon_facts('acme.invoice', 'acme.billing')


def test_distribution_without_packaged_toml_fails_without_python_import(tmp_path: Path) -> None:
    info = tmp_path / 'acme_invoice-1.dist-info'
    info.mkdir()
    with pytest.raises(ImproperlyConfigured, match='expected one packaged pyproject.toml'):
        package_document(metadata.PathDistribution(info), 'acme.invoice')


def test_explicit_package_index_is_reused_without_rescanning(tmp_path, monkeypatch):
    _distribution(tmp_path, '[tool.acme.billing]\ncurrency = "EUR"\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    index = installed_components("angee.addons")
    monkeypatch.setattr(addons, "installed_components", lambda group: pytest.fail("repeated metadata scan"))
    assert addon_facts("acme.invoice", "acme.billing", index=index) == {"currency": "EUR"}
    assert addon_facts("django.contrib.auth", "acme.billing", index=index) == {}


def test_composed_membership_uses_existing_closure(settings, monkeypatch):
    settings.COMPOSE_CLOSURE = enabled_apps(("angee.base",), group="angee.addons")
    monkeypatch.setattr(addons, "installed_components", lambda group: pytest.fail("repeated metadata scan"))
    assert addons.is_angee_addon(apps.get_app_config("base"))
    assert not addons.is_angee_addon(apps.get_app_config("auth"))


def test_graphql_discovery_scans_installed_metadata_once(monkeypatch):
    original = metadata.distributions
    calls = 0

    def distributions(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    monkeypatch.setattr(metadata, "distributions", distributions)
    graph = GraphQLSchemas((apps.get_app_config("base"), apps.get_app_config("resources")))
    assert graph.parts == {}
    assert calls == 1
