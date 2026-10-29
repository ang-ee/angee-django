"""Capability tables use one packaged-TOML reader and no disabled imports."""
from __future__ import annotations

import importlib
import json
from importlib import metadata
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.base.addons import addon_facts, package_document


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
    distribution = _distribution(tmp_path, '''
[tool.acme.billing]
currency = "EUR"
[tool.angee.platform]
category = "invoicing"
''', editable=editable)
    points = distribution.entry_points
    monkeypatch.setattr(metadata, 'entry_points', lambda **kwargs: points.select(**kwargs))
    monkeypatch.setattr(importlib, 'import_module', lambda *args, **kwargs: pytest.fail('disabled addon imported'))
    assert addon_facts('acme.invoice', 'acme.billing') == {'currency': 'EUR'}
    assert addon_facts('acme.invoice', 'angee.platform') == {'category': 'invoicing'}
    assert addon_facts('acme.invoice', 'angee.resources') == {}
    assert addon_facts('django.contrib.auth', 'angee.resources') == {}


def test_malformed_capability_is_rejected_at_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    distribution = _distribution(tmp_path, '[tool.acme]\nbilling = "wrong"\n')
    monkeypatch.setattr(metadata, 'entry_points', lambda **kwargs: distribution.entry_points.select(**kwargs))
    with pytest.raises(ImproperlyConfigured, match='tool.acme.billing must be a TOML table'):
        addon_facts('acme.invoice', 'acme.billing')


def test_distribution_without_packaged_toml_fails_without_python_import(tmp_path: Path) -> None:
    info = tmp_path / 'acme_invoice-1.dist-info'
    info.mkdir()
    with pytest.raises(ImproperlyConfigured, match='expected one packaged pyproject.toml'):
        package_document(metadata.PathDistribution(info), 'acme.invoice')
