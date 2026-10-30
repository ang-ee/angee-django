"""Startup declarations and request policy use their separate native authorities."""

import os
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from django.apps import AppConfig
from django.db import models
from django.test.utils import isolate_apps
from rebac.mixins import RebacModelBase
from rebac.schema import parse_zed

from angee.base import permissions
from angee.base.permissions import (
    bind_rebac_schema,
    declared_rebac_definition,
    declared_schema_path,
    effective_rebac_definition,
    effective_rebac_schema,
)
from angee.graphql.access import is_gated_read_axis


@isolate_apps()
def test_requests_use_synced_policy_without_reading_artifacts(monkeypatch):
    class Record(models.Model, metaclass=RebacModelBase):
        secret = models.CharField(max_length=30)
        class Meta:
            abstract = True
            app_label = 'policy_probe'
            rebac_resource_type = 'probe/record'

    schema = parse_zed(
        'definition probe/record {\npermission read = anonymous\npermission read__secret = anonymous\n}'
    )
    monkeypatch.setattr('angee.base.permissions.backend', lambda: SimpleNamespace(schema=lambda: schema))
    def forbid_read(*args, **kwargs):
        raise AssertionError('request read permission artifacts')
    monkeypatch.setattr(Path, 'read_text', forbid_read)
    assert effective_rebac_definition(Record) is schema.get_definition('probe/record')
    assert is_gated_read_axis(Record, 'secret')
    declared = parse_zed('definition probe/record { permission read = anonymous }')
    assert not is_gated_read_axis(
        Record, 'secret', definition_for=lambda model: declared.get_definition('probe/record'),
    )


def test_startup_snapshot_needs_no_policy_backend(tmp_path, monkeypatch):
    path = tmp_path / 'permissions.zed'
    path.write_text('definition probe/record { permission read = anonymous }')
    config = SimpleNamespace(path=str(tmp_path), rebac_schema=str(path))
    def forbid_backend():
        raise AssertionError('startup queried policy backend')
    monkeypatch.setattr('angee.base.permissions.backend', forbid_backend)
    assert effective_rebac_schema(config).get_definition('probe/record') is not None


def test_schema_binding_retains_source_and_honors_a_new_authored_override(tmp_path):
    class ProbeConfig(AppConfig):
        name = "probe"
        path = str(tmp_path)

    config = ProbeConfig("probe", ModuleType("probe"))
    source = tmp_path / "permissions.zed"
    source.write_text("definition probe/source {}")
    generated = tmp_path / "compiled.zed"
    generated.write_text("definition probe/compiled {}")
    override = tmp_path / "override.zed"
    override.write_text("definition probe/override {}")
    assert declared_schema_path(config) == source
    bind_rebac_schema(config, generated)
    assert declared_schema_path(config) == source
    assert effective_rebac_schema(config).get_definition("probe/compiled") is not None
    setattr(config, "rebac_schema", override)
    overridden = effective_rebac_schema(config)
    assert overridden.get_definition("probe/override") is not None
    assert effective_rebac_schema(config) is overridden
    assert declared_schema_path(config) == override
    bind_rebac_schema(config, generated)
    assert declared_schema_path(config) == override
    assert effective_rebac_schema(config).get_definition("probe/compiled") is not None
    bind_rebac_schema(config, None)
    assert effective_rebac_schema(config).get_definition("probe/override") is not None


def test_field_declarations_reuse_one_native_parse_and_file_read(tmp_path, monkeypatch):
    from tests.scopedemo.models import Scope

    config = permissions.apps.get_app_config(Scope._meta.app_label)
    path = tmp_path / "effective.zed"
    path.write_text("definition scopedemo/scope { permission read = anonymous }")
    monkeypatch.setattr(config, "rebac_schema", path, raising=False)
    parsed = []
    reads = []
    native_parse = permissions.parse_zed
    native_read = Path.read_text

    def parse(text):
        parsed.append(text)
        return native_parse(text)

    def read(source, *args, **kwargs):
        if source == path:
            reads.append(source)
        return native_read(source, *args, **kwargs)

    monkeypatch.setattr(permissions, "parse_zed", parse)
    monkeypatch.setattr(Path, "read_text", read)
    first = declared_rebac_definition(Scope)
    assert first is not None
    for _ in range(30):
        assert declared_rebac_definition(Scope) is first
    assert len(parsed) == len(reads) == 1


@pytest.mark.parametrize("atomic", [False, True])
def test_bound_schema_invalidates_equal_size_rewrites_with_restored_mtime(tmp_path, atomic):
    path = tmp_path / "permissions.zed"
    path.write_text("definition probe/record { permission read = anonymous }")
    config = SimpleNamespace(path=str(tmp_path), rebac_schema=path)
    first = effective_rebac_schema(config)
    status = path.stat()
    replacement = path.with_suffix(".new") if atomic else path
    replacement.write_text("definition probe/record { permission edit = anonymous }")
    os.utime(replacement, ns=(status.st_atime_ns, status.st_mtime_ns))
    if atomic:
        replacement.replace(path)
    assert path.stat().st_size == status.st_size
    assert path.stat().st_mtime_ns == status.st_mtime_ns
    second = effective_rebac_schema(config)
    assert second is not first
    assert [permission.name for permission in second.get_definition("probe/record").permissions] == ["edit"]


def test_missing_bound_schema_drops_its_parse_and_observes_restoration(tmp_path):
    path = tmp_path / "permissions.zed"
    text = "definition probe/record { permission read = anonymous }"
    path.write_text(text)
    config = SimpleNamespace(path=str(tmp_path), rebac_schema=path)
    first = effective_rebac_schema(config)
    path.unlink()
    assert effective_rebac_schema(config) is None
    path.write_text(text)
    restored = effective_rebac_schema(config)
    assert restored is not first and restored.get_definition("probe/record") is not None


def test_explicit_schema_rebinding_releases_the_held_parse(tmp_path):
    path = tmp_path / "permissions.zed"
    path.write_text("definition probe/record { permission read = anonymous }")
    config = SimpleNamespace(path=str(tmp_path), rebac_schema=path)
    first = effective_rebac_schema(config)
    bind_rebac_schema(config, path)
    second = effective_rebac_schema(config)
    assert second is not first
    assert effective_rebac_schema(config) is second
