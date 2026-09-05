"""The additive REBAC-schema extension seam (``angee.compose.permissions``).

A consumer addon contributes relations and permission arms to a definition owned
by another addon through a sibling ``permissions.extends.zed`` fragment, instead
of editing the owner's ``permissions.zed``. These tests pin the merge semantics
(carries the relation, unions the arm, fails fast on collision / missing target /
missing arm, deterministic), the round-trip renderer, and the full wiring:
emit + repoint + ``rebac sync`` so a tuple on the contributed relation resolves
through the local evaluator.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rebac import RelationshipTuple, to_subject_ref, write_relationships
from rebac.backends import backend
from rebac.schema.parser import parse_zed, validate_schema
from rebac.types import ObjectRef

from angee.compose.permissions import (
    SchemaExtensionError,
    apply_schema_paths,
    extension_source_map,
    merged_schema_relpath,
    merged_schemas,
    render_zed,
)
from angee.fs import write_atomic

User = get_user_model()

_BASE = """
// @rebac_package: base
// @rebac_schema_revision: 4
definition demo/thing {
    relation owner: auth/user

    permission read = owner
    permission write = owner
}
"""


def _addon(tmp_path: Path, name: str, filename: str, text: str) -> SimpleNamespace:
    """Write a zed file into a fresh addon dir and return an app-config stand-in."""

    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(text, encoding="utf-8")
    return SimpleNamespace(name=name, path=str(directory))


def _base_addon(tmp_path: Path, text: str = _BASE) -> SimpleNamespace:
    return _addon(tmp_path, "base", "permissions.zed", text)


def _contrib_addon(tmp_path: Path, text: str, name: str = "contrib") -> SimpleNamespace:
    return _addon(tmp_path, name, "permissions.extends.zed", text)


# ---------- merge semantics ----------


def test_dormant_without_fragments(tmp_path: Path) -> None:
    """With no ``permissions.extends.zed`` the seam is a no-op."""

    base = _base_addon(tmp_path)
    assert merged_schemas([base]) == {}
    assert extension_source_map([base]) == {}


def test_merge_carries_relation_and_unions_arm(tmp_path: Path) -> None:
    """A contribution adds its relation and unions its arm into the base permission."""

    base = _base_addon(tmp_path)
    contrib = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n    relation reviewer: auth/user\n    permission read = reviewer\n}\n",
    )
    merged = merged_schemas([base, contrib])
    assert set(merged) == {"base"}

    definition = merged["base"].get_definition("demo/thing")
    assert {relation.name for relation in definition.relations} == {"owner", "reviewer"}

    read = next(p for p in definition.permissions if p.name == "read")
    # base `owner` is preserved and `reviewer` is unioned in; `write` is untouched.
    assert _render_expr_names(read) == {"owner", "reviewer"}
    write = next(p for p in definition.permissions if p.name == "write")
    assert _render_expr_names(write) == {"owner"}
    assert not validate_schema(merged["base"])


def _render_expr_names(permission: object) -> set[str]:
    """Collect the relation/permission names an expression references."""

    from rebac.schema.ast import PermArrow, PermBinOp, PermNil, PermRef

    def walk(expr: object) -> set[str]:
        if isinstance(expr, PermRef):
            return {expr.name}
        if isinstance(expr, PermArrow):
            return {expr.via}
        if isinstance(expr, PermBinOp):
            return walk(expr.left) | walk(expr.right)
        if isinstance(expr, PermNil):
            return set()
        return set()

    return walk(permission.expression)


def test_missing_target_fails_fast(tmp_path: Path) -> None:
    """Extending a definition no installed package declares is an error."""

    base = _base_addon(tmp_path)
    contrib = _contrib_addon(
        tmp_path,
        "definition demo/absent {\n    relation reviewer: auth/user\n}\n",
    )
    with pytest.raises(SchemaExtensionError, match="demo/absent"):
        merged_schemas([base, contrib])


def test_relation_collision_fails_fast(tmp_path: Path) -> None:
    """A contributed relation cannot collide with a base relation."""

    base = _base_addon(tmp_path)
    contrib = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n    relation owner: auth/user\n}\n",
    )
    with pytest.raises(SchemaExtensionError, match="owner"):
        merged_schemas([base, contrib])


def test_two_contributors_same_relation_collides(tmp_path: Path) -> None:
    """Two fragments contributing the same relation name is a hard collision."""

    base = _base_addon(tmp_path)
    first = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n    relation reviewer: auth/user\n}\n",
        name="contrib_a",
    )
    second = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n    relation reviewer: auth/user\n}\n",
        name="contrib_b",
    )
    with pytest.raises(SchemaExtensionError, match="reviewer"):
        merged_schemas([base, first, second])


def test_arm_without_base_permission_fails_fast(tmp_path: Path) -> None:
    """A fragment can only union into an existing permission, not introduce one."""

    base = _base_addon(tmp_path)
    contrib = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n    relation reviewer: auth/user\n    permission approve = reviewer\n}\n",
    )
    with pytest.raises(SchemaExtensionError, match="approve"):
        merged_schemas([base, contrib])


def test_merge_is_deterministic(tmp_path: Path) -> None:
    """Two contributors merge in sorted composition order, byte-stable."""

    base = _base_addon(tmp_path)
    first = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n    relation auditor: auth/user\n    permission read = auditor\n}\n",
        name="contrib_a",
    )
    second = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n    relation reviewer: auth/user\n    permission read = reviewer\n}\n",
        name="contrib_b",
    )
    rendered = render_zed("base", merged_schemas([base, first, second])["base"])
    # Order of contributors on input must not change the output.
    assert rendered == render_zed("base", merged_schemas([base, second, first])["base"])
    assert "@rebac_extended_by: contrib_a@0, contrib_b@0" in rendered
    assert not validate_schema(parse_zed(rendered))


# ---------- renderer round-trip ----------


def test_render_round_trips_a_real_backed_schema() -> None:
    """The emitter re-parses to the same relations/backing/permissions.

    ``scopedemo`` exercises field-backed (``rebac:field``), const
    (``rebac:const``), and subject-union relations — the shapes a naive renderer
    drops.
    """

    source = Path(apps.get_app_config("scopedemo").path) / "permissions.zed"
    schema = parse_zed(source.read_text(encoding="utf-8"))
    reparsed = parse_zed(render_zed("tests.scopedemo", schema))
    assert not validate_schema(reparsed)

    original = schema.get_definition("scopedemo/scope")
    roundtripped = reparsed.get_definition("scopedemo/scope")
    assert {r.name for r in roundtripped.relations} == {r.name for r in original.relations}
    for relation in original.relations:
        emitted = next(r for r in roundtripped.relations if r.name == relation.name)
        assert emitted.backing == relation.backing
        assert set(emitted.allowed_subjects) == set(relation.allowed_subjects)
        assert emitted.with_expiration == relation.with_expiration
    assert {p.name for p in roundtripped.permissions} == {p.name for p in original.permissions}


def test_agents_tool_grants_accept_agent_and_role_subjects() -> None:
    """Tool use is granted on pure grant objects, not on MCP catalogue rows."""

    source = Path(apps.get_app_config("agents").path) / "permissions.zed"
    schema = parse_zed(source.read_text(encoding="utf-8"))

    server = schema.get_definition("agents/mcp_server")
    server_agent = next(relation for relation in server.relations if relation.name == "agent")
    assert {(subject.type, subject.id, subject.relation) for subject in server_agent.allowed_subjects} == {
        ("agents/agent", "", "")
    }

    tool = schema.get_definition("agents/mcp_tool")
    assert "agent" not in {relation.name for relation in tool.relations}

    grant = schema.get_definition("agents/tool_grant")
    grantee = next(relation for relation in grant.relations if relation.name == "grantee")
    assert {(subject.type, subject.id, subject.relation) for subject in grantee.allowed_subjects} == {
        ("agents/agent", "", ""),
        ("agents/toolrole", "", "effective_member"),
        ("auth/group", "", "agent_member"),
    }
    use = next(permission for permission in grant.permissions if permission.name == "use")
    assert "grantee" in _render_expr_names(use)


# ---------- full wiring: emit + repoint + sync + resolve ----------


@pytest.fixture
def _restore_scopedemo_schema():
    """Save/restore ``scopedemo``'s ``rebac_schema`` so the repoint stays scoped."""

    scopedemo = apps.get_app_config("scopedemo")
    sentinel = object()
    original = getattr(scopedemo, "rebac_schema", sentinel)
    yield scopedemo
    if original is sentinel:
        if hasattr(scopedemo, "rebac_schema"):
            delattr(scopedemo, "rebac_schema")
    else:
        scopedemo.rebac_schema = original


@pytest.mark.django_db
def test_contributed_relation_syncs_and_resolves(tmp_path: Path, _restore_scopedemo_schema) -> None:
    """``tests.extcontrib`` extends ``scopedemo/doc``; the merged schema syncs and a
    ``reviewer`` tuple resolves ``read`` through the local evaluator."""

    app_configs = list(apps.get_app_configs())
    runtime_dir = tmp_path / "runtime"

    # The composer/Runtime seam, driven directly (bare test settings skip the composer):
    # emit the merged zed, then repoint the owning app at it.
    source_map = extension_source_map(app_configs)
    assert merged_schema_relpath("tests.scopedemo") in source_map
    for relpath, text in source_map.items():
        write_atomic(runtime_dir / relpath, text)
    apply_schema_paths(app_configs, runtime_dir, sources=source_map)

    scopedemo = _restore_scopedemo_schema
    assert scopedemo.rebac_schema == str((runtime_dir / merged_schema_relpath("tests.scopedemo")).resolve())

    call_command("rebac", "sync", verbosity=0)

    from rebac.models import SchemaDefinition

    definition = SchemaDefinition.objects.get(resource_type="scopedemo/doc")
    assert definition.relations.filter(name="reviewer").exists()

    reviewer = User.objects.create_user(username="reviewer", email="reviewer@example.com")
    outsider = User.objects.create_user(username="outsider", email="outsider@example.com")
    doc = ObjectRef(resource_type="scopedemo/doc", resource_id="doc-1")
    write_relationships([RelationshipTuple(resource=doc, relation="reviewer", subject=to_subject_ref(reviewer))])

    assert backend().check_access(subject=to_subject_ref(reviewer), action="read", resource=doc)
    assert not backend().check_access(subject=to_subject_ref(outsider), action="read", resource=doc)


@pytest.mark.parametrize("declaration", [None, "custom.zed", "absolute"])
def test_custom_base_sources_survive_repeated_composition(tmp_path, declaration):
    base = _base_addon(tmp_path)
    if declaration is not None:
        source = Path(base.path) / "custom.zed"
        (Path(base.path) / "permissions.zed").rename(source)
        base.rebac_schema = str(source) if declaration == "absolute" else declaration
    else:
        base.rebac_schema = None
    contrib = _contrib_addon(
        tmp_path,
        "definition demo/thing { relation viewer: auth/user permission read = viewer }",
    )
    configs = [base, contrib]
    expected = extension_source_map(configs)
    runtime = tmp_path / "runtime"
    # Repointing precedes emission during first composition.
    apply_schema_paths(configs, runtime, sources=expected)
    assert extension_source_map(configs) == expected
    for relative, text in expected.items():
        output = runtime / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    apply_schema_paths(configs, runtime, sources=expected)
    assert extension_source_map(configs) == expected
    schema = merged_schemas(configs)["base"]
    assert len(schema.get_definition("demo/thing").relations) == 2


def test_render_retains_authored_metadata_and_directives():
    schema = parse_zed(
        "// @rebac_package_version: 4.2\nuse typechecking\ndefinition demo/item { permission read = anonymous }"
    )
    output = parse_zed(render_zed("demo", schema))
    assert output.headers["rebac_package_version"] == "4.2"
    assert output.directives == schema.directives
    assert "rebac_package" not in schema.headers


def test_effective_source_rewrites_do_not_leave_stale_field_gates(tmp_path, monkeypatch):
    from angee.base.permissions import effective_rebac_definition
    from tests.scopedemo.models import Scope

    config = apps.get_app_config(Scope._meta.app_label)
    path = tmp_path / "effective.zed"
    monkeypatch.setattr(config, "rebac_schema", str(path), raising=False)
    path.write_text("definition scopedemo/scope { permission read = anonymous }")
    first = effective_rebac_definition(Scope)
    assert [p.name for p in first.permissions] == ["read"]
    path.write_text("definition scopedemo/scope { permission read = anonymous permission read__name = nil }")
    second = effective_rebac_definition(Scope)
    assert [p.name for p in second.permissions] == ["read", "read__name"]


def test_manifest_permission_path_wins_over_convention(tmp_path):
    """The parsed manifest stays authoritative after repeated effective binding."""

    base = _base_addon(tmp_path)
    custom = Path(base.path) / "authored.zed"
    custom.write_text(_BASE.replace("demo/thing", "demo/custom"))
    (Path(base.path) / "addon.toml").write_text('[addon]\nname = "base"\npermissions = "authored.zed"\n')
    contrib = _contrib_addon(
        tmp_path,
        "definition demo/custom { relation viewer: auth/user permission read = viewer }",
    )
    configs = [base, contrib]
    sources = extension_source_map(configs)
    apply_schema_paths(configs, tmp_path / "runtime", sources=sources)
    assert extension_source_map(configs) == sources
    assert "demo/custom" in sources[merged_schema_relpath("base")]
    assert "demo/thing" not in sources[merged_schema_relpath("base")]


def test_manifest_permission_file_is_checked_before_any_fragment(tmp_path):
    """An explicit missing declaration fails even when no extension is installed."""

    from django.core.exceptions import ImproperlyConfigured

    base = _base_addon(tmp_path)
    (Path(base.path) / "addon.toml").write_text('[addon]\nname = "base"\npermissions = "missing.zed"\n')
    with pytest.raises(ImproperlyConfigured, match="declared permissions file does not exist"):
        extension_source_map([base])


def test_permission_binding_does_not_repeat_merge(tmp_path, monkeypatch):
    """Binding uses the exact rendered map, without parsing source files again."""

    import angee.compose.permissions as permissions

    base = _base_addon(tmp_path)
    contrib = _contrib_addon(tmp_path, "definition demo/thing { permission read = anonymous }")
    configs = [base, contrib]
    sources = extension_source_map(configs)
    monkeypatch.setattr(permissions, "merged_schemas", lambda _: pytest.fail("binding remerged schemas"))
    apply_schema_paths(configs, tmp_path / "runtime", sources=sources)
    assert base.rebac_schema == str((tmp_path / "runtime" / merged_schema_relpath("base")).resolve())


def test_native_schema_binding_restores_source_when_extensions_are_removed(tmp_path):
    """Removing an extension cannot leave a native app pointing at pruned output."""

    base = _base_addon(tmp_path)
    contrib = _contrib_addon(tmp_path, "definition demo/thing { permission read = anonymous }")
    configs = [base, contrib]
    sources = extension_source_map(configs)
    apply_schema_paths(configs, tmp_path / "runtime", sources=sources)
    apply_schema_paths([base], tmp_path / "runtime", sources=extension_source_map([base]))
    assert base.rebac_schema == str((Path(base.path) / "permissions.zed").resolve())


def test_native_schema_declaration_can_change_between_compositions(tmp_path):
    """A plain Django AppConfig retains its upstream declaration authority."""

    base = _base_addon(tmp_path)
    contrib = _contrib_addon(tmp_path, "definition demo/thing { permission read = anonymous }")
    sources = extension_source_map([base, contrib])
    apply_schema_paths([base, contrib], tmp_path / "runtime", sources=sources)
    replacement = Path(base.path) / "replacement.zed"
    replacement.write_text(_BASE.replace("demo/thing", "demo/replacement"))
    base.rebac_schema = "replacement.zed"
    apply_schema_paths([base], tmp_path / "runtime", sources=extension_source_map([base]))
    assert base.rebac_schema == str(replacement.resolve())
