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
        "definition demo/thing {\n"
        "    relation reviewer: auth/user\n"
        "    permission read = reviewer\n"
        "}\n",
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
        "definition demo/thing {\n"
        "    relation reviewer: auth/user\n"
        "    permission approve = reviewer\n"
        "}\n",
    )
    with pytest.raises(SchemaExtensionError, match="approve"):
        merged_schemas([base, contrib])


def test_merge_is_deterministic(tmp_path: Path) -> None:
    """Two contributors merge in sorted composition order, byte-stable."""

    base = _base_addon(tmp_path)
    first = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n"
        "    relation auditor: auth/user\n"
        "    permission read = auditor\n"
        "}\n",
        name="contrib_a",
    )
    second = _contrib_addon(
        tmp_path,
        "definition demo/thing {\n"
        "    relation reviewer: auth/user\n"
        "    permission read = reviewer\n"
        "}\n",
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

    IAM's ``permissions.zed`` exercises field-backed (``rebac:field``) and const
    (``rebac:const``) relations plus a specific-id subject — the shapes a naive
    renderer drops.
    """

    source = Path(apps.get_app_config("iam").path) / "permissions.zed"
    schema = parse_zed(source.read_text(encoding="utf-8"))
    reparsed = parse_zed(render_zed("angee.iam", schema))
    assert not validate_schema(reparsed)

    original = schema.get_definition("iam/company")
    roundtripped = reparsed.get_definition("iam/company")
    assert {r.name for r in roundtripped.relations} == {r.name for r in original.relations}
    for relation in original.relations:
        emitted = next(r for r in roundtripped.relations if r.name == relation.name)
        assert type(emitted.backing) is type(relation.backing)
        assert {(s.type, s.id, s.relation) for s in emitted.allowed_subjects} == {
            (s.type, s.id, s.relation) for s in relation.allowed_subjects
        }
    assert {p.name for p in roundtripped.permissions} == {p.name for p in original.permissions}


def test_agents_mcp_relations_accept_agent_subjects() -> None:
    """Agents may be direct subjects only on the agents addon's MCP access relations."""

    source = Path(apps.get_app_config("agents").path) / "permissions.zed"
    schema = parse_zed(source.read_text(encoding="utf-8"))

    for resource_type in ("agents/mcp_server", "agents/mcp_tool"):
        definition = schema.get_definition(resource_type)
        relation = next(relation for relation in definition.relations if relation.name == "agent")
        assert {(subject.type, subject.id, subject.relation) for subject in relation.allowed_subjects} == {
            ("agents/agent", "", "")
        }
        read = next(permission for permission in definition.permissions if permission.name == "read")
        assert "agent" in _render_expr_names(read)


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
    apply_schema_paths(app_configs, runtime_dir)

    scopedemo = _restore_scopedemo_schema
    assert scopedemo.rebac_schema == str(
        (runtime_dir / merged_schema_relpath("tests.scopedemo")).resolve()
    )

    call_command("rebac", "sync", verbosity=0)

    from rebac.models import SchemaDefinition

    definition = SchemaDefinition.objects.get(resource_type="scopedemo/doc")
    assert definition.relations.filter(name="reviewer").exists()

    reviewer = User.objects.create_user(username="reviewer", email="reviewer@example.com")
    outsider = User.objects.create_user(username="outsider", email="outsider@example.com")
    doc = ObjectRef(resource_type="scopedemo/doc", resource_id="doc-1")
    write_relationships(
        [RelationshipTuple(resource=doc, relation="reviewer", subject=to_subject_ref(reviewer))]
    )

    assert backend().check_access(subject=to_subject_ref(reviewer), action="read", resource=doc)
    assert not backend().check_access(subject=to_subject_ref(outsider), action="read", resource=doc)
