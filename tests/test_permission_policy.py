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
    contextual_action_holds,
    declared_rebac_definition,
    declared_rebac_schema,
    declared_schema_path,
    effective_rebac_definition,
    purpose_read_holds,
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


def _policy(monkeypatch, effective: str, declared: str | None = None) -> None:
    """Serve ``effective`` as the synced policy and ``declared`` (else the same) as each type's declaration."""

    stated = parse_zed(declared if declared is not None else effective)
    synced = parse_zed(effective)
    monkeypatch.setattr('angee.base.permissions.backend', lambda: SimpleNamespace(schema=lambda: synced))
    # Each resource type stands for its own model, whose declaration is ``stated``'s definition.
    monkeypatch.setattr('angee.base.permissions.model_for_resource_type', lambda resource_type: resource_type)
    monkeypatch.setattr('angee.base.permissions.declared_rebac_definition', stated.get_definition)


_PARENT = (
    "definition probe/parent {{\nrelation reader: auth/user\nrelation blocked: auth/user\n"
    "permission read = {read}\npermission read_inbox = reader\n}}\n"
)


@pytest.mark.parametrize(
    ("read", "read_inbox", "parent_read", "holds"),
    [
        ("parent->read + reader", "parent->read", "reader", True),
        ("parent->read + (reader - blocked)", "parent->read", "reader", True),
        # The parent's narrower action holds while the parent states it as declared.
        ("parent->read + reader", "parent->read_inbox", "reader", True),
        ("parent->read + reader", "parent->read_inbox", "reader & blocked", False),
        # A parent override is irrelevant while the child reads the parent's own read.
        ("parent->read + reader", "parent->read", "reader & blocked", True),
        # A tightening override, an exclusion, or a narrowed parent arm is unproved.
        ("(parent->read + reader) & reader", "parent->read", "reader", False),
        ("(parent->read + reader) - blocked", "parent->read", "reader", False),
        ("(parent->read - blocked) + reader", "parent->read", "reader", False),
        ("reader + blocked", "parent->read", "reader", False),
        # An extended contextual action admits rows of unreadable parents.
        ("parent->read + reader", "parent->read + reader", "reader", False),
        ("parent->read + reader", None, "reader", False),
    ],
)
@isolate_apps()
def test_contextual_action_holds_only_while_the_effective_policy_proves_it(
    monkeypatch, read, read_inbox, parent_read, holds,
):
    class Record(models.Model, metaclass=RebacModelBase):
        class Meta:
            abstract = True
            app_label = 'policy_probe'
            rebac_resource_type = 'probe/record'

    contextual = f"permission read_inbox = {read_inbox}" if read_inbox else ""
    record = (
        "definition probe/record {\nrelation parent: probe/parent\nrelation reader: auth/user\n"
        f"relation blocked: auth/user\npermission read = {read}\n{contextual}\n}}"
    )
    _policy(monkeypatch, _PARENT.format(read=parent_read) + record, _PARENT.format(read="reader") + record)
    assert contextual_action_holds(Record, "read_inbox") is holds


def _purpose(read: str, read_inbox: str | None, read_granted: str) -> str:
    contextual = f"permission read_inbox = {read_inbox}\n" if read_inbox else ""
    return (
        "definition probe/record {\nrelation parent: probe/parent\nrelation reader: auth/user\n"
        "relation chatter: auth/user\nrelation blocked: auth/user\n"
        f"permission read_granted = {read_granted}\npermission read = {read}\n{contextual}}}"
    )


_DECLARED = ("parent->read + read_granted + chatter", "parent->read_inbox + read_granted", "reader")


@pytest.mark.parametrize(
    ("synced", "parent_read", "holds"),
    [
        (_DECLARED, "reader", True),
        # An override on either derived read, extending or tightening, leaves the purpose read unproved.
        (("(parent->read + read_granted + chatter) & reader", *_DECLARED[1:]), "reader", False),
        (("parent->read + read_granted + chatter + blocked", *_DECLARED[1:]), "reader", False),
        ((_DECLARED[0], "(parent->read_inbox + read_granted) - blocked", _DECLARED[2]), "reader", False),
        # One on the extension point both reads take keeps it.
        ((*_DECLARED[:2], "reader & blocked"), "reader", True),
        # The parent purpose read its arrow reaches must hold too.
        (_DECLARED, "reader + blocked", False),
        # A store not yet synced to the declaration states no purpose read.
        ((_DECLARED[0], None, _DECLARED[2]), "reader", False),
    ],
)
@isolate_apps()
def test_a_purpose_read_holds_only_while_the_derived_reads_are_stated_as_declared(
    monkeypatch, synced, parent_read, holds,
):
    class Record(models.Model, metaclass=RebacModelBase):
        class Meta:
            abstract = True
            app_label = 'policy_probe'
            rebac_resource_type = 'probe/record'

    _policy(
        monkeypatch,
        _PARENT.format(read=parent_read) + _purpose(*synced),
        _PARENT.format(read="reader") + _purpose(*_DECLARED),
    )
    assert purpose_read_holds(Record, "read_inbox") is holds


def test_startup_snapshot_needs_no_policy_backend(tmp_path, monkeypatch):
    path = tmp_path / 'permissions.zed'
    path.write_text('definition probe/record { permission read = anonymous }')
    config = SimpleNamespace(path=str(tmp_path), rebac_schema=str(path))
    def forbid_backend():
        raise AssertionError('startup queried policy backend')
    monkeypatch.setattr('angee.base.permissions.backend', forbid_backend)
    assert declared_rebac_schema(config).get_definition('probe/record') is not None


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
    assert declared_rebac_schema(config).get_definition("probe/compiled") is not None
    setattr(config, "rebac_schema", override)
    overridden = declared_rebac_schema(config)
    assert overridden.get_definition("probe/override") is not None
    assert declared_rebac_schema(config) is overridden
    assert declared_schema_path(config) == override
    bind_rebac_schema(config, generated)
    assert declared_schema_path(config) == override
    assert declared_rebac_schema(config).get_definition("probe/compiled") is not None
    bind_rebac_schema(config, None)
    assert declared_rebac_schema(config).get_definition("probe/override") is not None


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
    first = declared_rebac_schema(config)
    status = path.stat()
    replacement = path.with_suffix(".new") if atomic else path
    replacement.write_text("definition probe/record { permission edit = anonymous }")
    os.utime(replacement, ns=(status.st_atime_ns, status.st_mtime_ns))
    if atomic:
        replacement.replace(path)
    assert path.stat().st_size == status.st_size
    assert path.stat().st_mtime_ns == status.st_mtime_ns
    second = declared_rebac_schema(config)
    assert second is not first
    assert [permission.name for permission in second.get_definition("probe/record").permissions] == ["edit"]


def test_missing_bound_schema_drops_its_parse_and_observes_restoration(tmp_path):
    path = tmp_path / "permissions.zed"
    text = "definition probe/record { permission read = anonymous }"
    path.write_text(text)
    config = SimpleNamespace(path=str(tmp_path), rebac_schema=path)
    first = declared_rebac_schema(config)
    path.unlink()
    assert declared_rebac_schema(config) is None
    path.write_text(text)
    restored = declared_rebac_schema(config)
    assert restored is not first and restored.get_definition("probe/record") is not None


def test_explicit_schema_rebinding_releases_the_held_parse(tmp_path):
    path = tmp_path / "permissions.zed"
    path.write_text("definition probe/record { permission read = anonymous }")
    config = SimpleNamespace(path=str(tmp_path), rebac_schema=path)
    first = declared_rebac_schema(config)
    bind_rebac_schema(config, path)
    second = declared_rebac_schema(config)
    assert second is not first
    assert declared_rebac_schema(config) is second
