"""The message template library: rows, shipped defaults and overrides, languages, access and the console."""

from __future__ import annotations

import time
from typing import Any

import pytest
import tablib
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import Collector
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, SubjectRef, actor_context, system_context
from rebac.testing import assert_decisions

from angee.base.models.identity import public_id_of
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Person
from angee.messaging_templates import schema as messaging_templates_schema
from angee.messaging_templates.models import (
    NO_BASELINE_MESSAGE,
    NOT_A_BASELINE_MESSAGE,
    LayoutHandler,
    MessageTemplateKind,
)
from angee.messaging_templates.rendering import RESERVED_VARIABLE_ROOTS, validate_variables
from angee.messaging_templates.testing.models import MessageTemplate
from angee.resources.entries import ResourceEntry, ResourceGroup
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from tests.conftest import addon_schema, create_platform_admin, create_user
from tests.decision_table import seat_cells

_KIND_VALUES: dict[str, dict[str, Any]] = {
    "layout": {"format": "html", "source": "<main>Body</main>", "text_source": "Body"},
    "template": {"format": "markdown", "source": "Hello.", "subject": "Hello", "layout": "base"},
    "block": {"format": "html", "source": "<p>Block</p>", "text_source": "Block"},
    "part": {"format": "markdown", "source": "Part."},
}


def _row(
    kind: str = "layout", key: str = "base", language: str = "en", *, baseline: bool = False, **values: Any,
) -> Any:
    """Create one row as named system work, filled as its kind requires."""

    fields = {"name": f"{kind} {key} {language}", **_KIND_VALUES[kind], **values}
    with system_context(reason="tests.messaging_templates.setup"):
        return MessageTemplate.objects.create(kind=kind, key=key, language=language, is_baseline=baseline, **fields)


def _read(row: Any) -> Any:
    with system_context(reason="tests.messaging_templates.read"):
        return MessageTemplate.objects.get(pk=row.pk)


def _system_clean(row: Any) -> dict[str, list[str]]:
    """Return the full clean's errors for ``row`` as named system work."""

    with system_context(reason="tests.messaging_templates.clean"):
        try:
            row.full_clean()
        except ValidationError as error:
            return error.message_dict
    return {}


def _unsaved(kind: str = "layout", key: str = "base", language: str = "en", **values: Any) -> Any:
    return MessageTemplate(kind=kind, key=key, language=language, **{"name": key, **_KIND_VALUES[kind], **values})


def _schema() -> Any:
    return addon_schema(messaging_templates_schema.schemas, "console")


# ---------- the rows ----------


@pytest.mark.parametrize(
    ("kind", "values"),
    [
        pytest.param("layout", {"subject": "A subject"}, id="layout-with-a-subject"),
        pytest.param("block", {"layout": "base"}, id="block-with-a-layout"),
        pytest.param("part", {"preheader": "Preview"}, id="part-with-a-preheader"),
        pytest.param("template", {"layout": ""}, id="template-without-a-layout"),
        pytest.param("layout", {"format": "markdown", "text_source": ""}, id="markdown-layout"),
        pytest.param("block", {"text_source": ""}, id="html-without-a-text-form"),
        pytest.param("part", {"text_source": "Text"}, id="markdown-with-a-text-form"),
        pytest.param("layout", {"baseline_hash": "sha256:x", "is_baseline": True}, id="baseline-overriding-something"),
    ],
)
def test_the_database_holds_each_kind_only_column_and_each_format(db: None, kind: str, values: dict[str, Any]) -> None:
    """A write past ``clean`` still meets the constraints the kind handlers and formats declare."""

    del db
    baseline = values.pop("is_baseline", False)
    with pytest.raises(IntegrityError), transaction.atomic():
        _row(kind, "tests.refused" if kind == "template" else "refused", baseline=baseline, **values)


def test_one_baseline_and_one_deployment_row_per_kind_key_and_language(db: None) -> None:
    del db
    _row(baseline=True)
    _row()
    for baseline in (True, False):
        with pytest.raises(IntegrityError), transaction.atomic():
            _row(baseline=baseline)
    _row(language="es")
    _row("block", key="base")
    assert _system_clean(_unsaved()) == {
        "__all__": ["This deployment already has a message template with this kind, key and language."],
    }


def test_the_full_clean_names_a_broken_kind_rule(db: None) -> None:
    del db
    assert _system_clean(_unsaved("part", key="signature", layout="base")) == {
        "__all__": ["A template names its layout; a layout, block or part has no subject, preheader or layout."],
    }
    assert _system_clean(_unsaved("layout", key="plain", format="markdown", text_source="")) == {
        "__all__": ["A layout is written in HTML."],
    }


@pytest.mark.parametrize(
    ("kind", "values", "field"),
    [
        pytest.param("layout", {"key": "Base"}, "key", id="upper-case-key"),
        pytest.param("layout", {"key": "base."}, "key", id="dangling-dot"),
        pytest.param("layout", {"key": "base\n"}, "key", id="trailing-newline"),
        pytest.param("layout", {"key": "mail.base"}, "key", id="layout-key-with-a-label"),
        pytest.param("template", {"key": "welcome"}, "key", id="template-key-without-a-label"),
        pytest.param("layout", {"language": "EN"}, "language", id="language-not-as-parties-stores-it"),
        pytest.param("layout", {"language": "eng"}, "language", id="three-letters-for-a-two-letter-language"),
        pytest.param("layout", {"language": "xx"}, "language", id="not-a-language"),
        pytest.param("layout", {"language": "tlh"}, "language", id="a-language-django-does-not-know"),
        pytest.param(
            "template", {"key": "tests.welcome", "layout": "missing"}, "layout", id="layout-that-does-not-resolve",
        ),
        pytest.param("layout", {"variables": {"Order.total": "x"}}, "variables", id="variable-path-case"),
        pytest.param("layout", {"variables": []}, "variables", id="variables-not-an-object"),
        pytest.param("layout", {"sample": ["Ana"]}, "sample", id="sample-not-an-object"),
    ],
)
def test_a_row_the_structure_refuses(db: None, kind: str, values: dict[str, Any], field: str) -> None:
    del db
    _row(baseline=True)  # the layout "base" resolves
    row = _unsaved(kind, **{"key": "tests.welcome" if kind == "template" else "plain", **values})
    assert field in _system_clean(row)


def test_a_template_needs_its_layout_in_the_default_language_or_shipped(db: None) -> None:
    """A deployment's layout only in German does not resolve for every recipient; a shipped one in any language does."""

    del db
    template = _unsaved("template", key="tests.welcome", layout="letter")
    _row(key="letter", language="de")
    assert "layout" in _system_clean(template)
    _row(key="letter", language="fr", baseline=True)
    assert _system_clean(template) == {}


_NINE_SEGMENTS = "a.b.c.d.e.f.g.h.i"


@pytest.mark.parametrize(
    ("variables", "code", "named"),
    [
        pytest.param({"order.total": "Total", "tags.*": "Tags", "items.*.name": "Names"}, None, "", id="admitted"),
        pytest.param({"order": "Order", "order.total": "Total"}, "variable_prefix", "order", id="prefix-and-path"),
        pytest.param({"tags": "", "tags.*": ""}, "variable_prefix", "tags", id="list-and-its-items"),
        pytest.param({"order": "", "order_id": "", "orders.*": ""}, None, "", id="names-sharing-letters"),
        pytest.param({"*": ""}, "invalid_variable_path", "*", id="star-root"),
        pytest.param({"order..total": ""}, "invalid_variable_path", "order..total", id="empty-segment"),
        pytest.param({"order.1": ""}, "invalid_variable_path", "order.1", id="index-segment"),
        pytest.param({"order.total": 3}, "invalid_variable_description", "order.total", id="description-not-words"),
        pytest.param(["order.total"], "invalid_variables", "", id="not-an-object"),
        *(pytest.param({f"{root}.name": ""}, "reserved_variable", f"{root}.name", id=f"reserved-{root}")
          for root in sorted(RESERVED_VARIABLE_ROOTS)),
        pytest.param({f"v{index}": "" for index in range(51)}, "too_many_variables", "v50", id="more-than-fifty"),
        pytest.param({_NINE_SEGMENTS[:-2]: ""}, None, "", id="eight-segments"),
        pytest.param({_NINE_SEGMENTS: ""}, "variable_too_deep", _NINE_SEGMENTS, id="nine-segments"),
        pytest.param({"items.*": "", "items.pop": ""}, "variable_list_and_object", "items.pop", id="list-then-object"),
        pytest.param(
            {"items.pop": "", "items.*.name": ""}, "variable_list_and_object", "items.*.name", id="object-then-list",
        ),
        pytest.param({"a.b.*": "", "a.b.c": ""}, "variable_list_and_object", "a.b.c", id="nested-list-and-object"),
    ],
)
def test_the_grammar_of_declared_variables(variables: Any, code: str | None, named: str) -> None:
    """Each refusal says which rule it broke and names the key that broke it."""

    if code is None:
        validate_variables(variables)
        return
    with pytest.raises(ValidationError) as refused:
        validate_variables(variables)
    assert refused.value.code == code
    assert named in refused.value.messages[0]


def test_a_caller_may_declare_more_paths_than_a_row() -> None:
    """A row declares at most fifty paths; the renderer judges a template with its layout against its own limit."""

    paths = {f"v{index}.name": "" for index in range(51)}
    with pytest.raises(ValidationError) as refused:
        validate_variables(paths)
    assert refused.value.code == "too_many_variables"
    validate_variables(paths, max_paths=100)


def test_declared_variables_are_judged_in_linear_time() -> None:
    """A console insert runs the grammar before it checks who may insert, so a hostile declaration stays cheap."""

    parent = ".".join(["a" * 100_000] * 7)
    hostile = [
        {f"v{index}": "" for index in range(1_000_000)},  # very many keys
        {".".join(["a"] * 1_000_000): ""},  # a very deep key
        {"a" * 1_000_000 + "!": ""},  # a very long name
        {f"{parent}.b": "", f"{parent}.*": ""},  # long keys reading one value as an object and a list
    ]
    started = time.perf_counter()
    for variables in hostile:
        with pytest.raises(ValidationError):
            validate_variables(variables)
    admitted = ".".join(["a" * 10_000] * 7)
    validate_variables({f"{admitted}.v{index}": "" for index in range(50)})
    assert time.perf_counter() - started < 2


def test_every_save_fingerprints_what_renders(db: None) -> None:
    """``content_hash`` follows the rendered columns, partial saves included, and ignores the name."""

    del db
    row = _row(variables={"name": "Who"})
    first = row.content_hash
    assert first.startswith("sha256:") and len(first) == 71
    with system_context(reason="tests.messaging_templates.edit"):
        row.name = "Renamed"
        row.save(update_fields=["name"])
        assert _read(row).content_hash == first
        row.source = "<main>Changed</main>"
        row.save(update_fields=["source"])
    changed = _read(row).content_hash
    assert changed != first
    with system_context(reason="tests.messaging_templates.edit"):
        row.save()
    assert _read(row).content_hash == changed


def test_the_kind_key_language_and_flag_are_written_once(db: None) -> None:
    del db
    row = _row()
    with system_context(reason="tests.messaging_templates.edit"):
        for field, value in (("kind", "block"), ("key", "other"), ("language", "es"), ("is_baseline", True)):
            row = _read(row)
            setattr(row, field, value)
            with pytest.raises(ValidationError) as refused, transaction.atomic():
                row.save()
            assert set(refused.value.message_dict) == {field}


def test_each_kind_has_its_handler() -> None:
    assert MessageTemplateKind.LAYOUT.handler.template_prefix == "layouts/"
    assert MessageTemplateKind.BLOCK.handler.template_prefix == "blocks/"
    assert MessageTemplateKind.PART.handler.template_prefix == "parts/"
    assert MessageTemplateKind.TEMPLATE.handler.template_prefix == ""
    carrying = [kind for kind in MessageTemplateKind if kind.handler.carries_message_fields]
    assert carrying == [MessageTemplateKind.TEMPLATE]
    assert isinstance(MessageTemplate(kind="layout").handler, LayoutHandler)


# ---------- access ----------


def test_the_policy_decides_every_message_template_permission(db: None) -> None:
    """Every signed-in principal reads; admins alone read samples and write rows but baselines, which nobody writes."""

    del db
    seats = {
        "admin": create_platform_admin("messaging-templates-policy-admin"),
        "member": create_user("messaging-templates-policy-member"),
        "service": SubjectRef.of("service/principal", "messaging-templates-policy"),
        "anonymous": AnonymousUser(),
    }
    baseline = _row(baseline=True, sample={"name": "Ana"})
    override = _row()
    own = _row(key="letter")
    rows = []
    for row in (baseline, override, own):
        editors = "" if row.is_baseline else "admin"
        others = "admin member service anonymous" if row.is_baseline else "member service anonymous"
        rows += [
            (row, "read", "admin member service", "anonymous"),
            (row, "read__sample", "admin", "member service anonymous"),
            *((row, action, editors, others) for action in ("create", "write", "delete")),
        ]
    assert_decisions(checks=seat_cells(seats.__getitem__, *rows))
    # The scope lists every row; the backend's id listing would also name the const ``baseline`` target.
    for seat in ("member", "service"):
        scoped = MessageTemplate.objects.with_actor(seats[seat]).order_by("pk").values_list("pk", flat=True)
        assert list(scoped) == [baseline.pk, override.pk, own.pk]
    assert not MessageTemplate.objects.with_actor(seats["anonymous"]).exists()


def test_a_member_reads_rows_and_no_sample_and_cannot_write(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-admin")
    member = create_user("messaging-templates-member")
    _row(baseline=True, sample={"name": "Ana"})
    document = "{ message_templates { key sample } }"
    assert result_data(execute_schema(_schema(), document, user=admin))["message_templates"] == [
        {"key": "base", "sample": {"name": "Ana"}},
    ]
    assert result_data(execute_schema(_schema(), document, user=member))["message_templates"] == [
        {"key": "base", "sample": None},
    ]
    anonymous = execute_schema(_schema(), document, user=AnonymousUser())
    assert anonymous.data is None or anonymous.data["message_templates"] == []


_INSERT = """
mutation($object: message_templates_insert_input!) {
  insert_message_templates_one(object: $object) { id kind key language is_baseline is_effective }
}
"""
_UPDATE = """
mutation($id: String!, $set: message_templates_set_input!) {
  update_message_templates_by_pk(pk_columns: {id: $id}, _set: $set) { id source }
}
"""
_DELETE = "mutation($id: String!) { delete_message_templates_by_pk(id: $id) { id } }"


def test_an_admin_writes_the_deployments_rows_and_nobody_writes_a_baseline(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-writer")
    member = create_user("messaging-templates-onlooker")
    part = {"kind": "part", "key": "signature", "language": "en", "name": "Signature", "format": "markdown",
            "source": "The team", "variables": {"team.name": "The team's name"}}
    for refused in (member, AnonymousUser()):
        result = execute_schema(_schema(), _INSERT, {"object": part}, user=refused)
        assert result.errors
        assert result.errors[0].extensions["code"] == "PERMISSION_DENIED"
    created = result_data(execute_schema(_schema(), _INSERT, {"object": part}, user=admin))
    assert created["insert_message_templates_one"] == {
        "id": created["insert_message_templates_one"]["id"], "kind": "PART", "key": "signature", "language": "en",
        "is_baseline": False, "is_effective": True,
    }

    baseline = _row(baseline=True)
    edit = {"id": public_id_of(baseline), "set": {"source": "<main>Edited</main>"}}
    assert execute_schema(_schema(), _UPDATE, edit, user=admin).errors
    assert execute_schema(_schema(), _DELETE, {"id": public_id_of(baseline)}, user=admin).errors
    assert _read(baseline).source == "<main>Body</main>"


# ---------- shipped defaults: the resource adapter and reload ----------


def _load(*rows: tuple[Any, ...], headers: list[str]) -> Any:
    addon = apps.get_app_config("messaging_templates")
    entry = ResourceEntry(
        addon=addon,
        tier=Resource.Tier.INSTALL,
        source_value="resources/install/010_messaging_templates.messagetemplate.yaml",
        model=MessageTemplate._meta.label,
    )
    group = ResourceGroup(entry, MessageTemplate._meta.label, tablib.Dataset(*rows, headers=headers), [1])
    aliases = {addon.label: addon.name}
    return Resource.objects._import_groups((entry,), (group,), (), dry_run=False, addon_aliases=aliases)


_LAYOUT_HEADERS = ["_xref", "kind", "key", "language", "name", "format", "source", "text_source"]


@pytest.mark.parametrize(
    ("column", "refusal"),
    [
        ("is_baseline", "always install baselines"),
        ("content_hash", "never state this column"),
        ("baseline_hash", "never state this column"),
    ],
)
def test_a_resource_row_never_states_the_flag_or_a_hash(db: None, column: str, refusal: str) -> None:
    del db
    row = ("base", "layout", "base", "en", "Base", "html", "<main/>", "Body", "")
    with pytest.raises(ResourceLoadError, match=refusal):
        _load(row, headers=[*_LAYOUT_HEADERS, column])
    with system_context(reason="tests.messaging_templates.read"):
        assert not MessageTemplate.objects.exists()


def test_a_reload_of_a_changed_default_stores_its_fingerprint_and_shows_default_updated(db: None) -> None:
    """The reload saves only the row's columns, the hash with them, and the override sees the default moved."""

    del db
    first = _load(("base", "layout", "base", "en", "Base", "html", "<main>One</main>", "One"), headers=_LAYOUT_HEADERS)
    assert (first.created, first.updated) == (1, 0)
    with system_context(reason="tests.messaging_templates.read"):
        baseline = MessageTemplate.objects.get(key="base")
        assert baseline.is_baseline
        override = MessageTemplate.objects.override(baseline)
    shipped_before = baseline.content_hash
    assert override.baseline_hash == shipped_before
    assert not _read(override).default_updated

    second = _load(("base", "layout", "base", "en", "Base", "html", "<main>Two</main>", "Two"), headers=_LAYOUT_HEADERS)
    assert (second.created, second.updated) == (0, 1)
    reloaded = _read(baseline)
    assert reloaded.source == "<main>Two</main>"
    assert reloaded.content_hash != shipped_before
    with system_context(reason="tests.messaging_templates.resave"):
        reloaded.save()
    assert _read(baseline).content_hash == reloaded.content_hash  # the reload stored the hash a full save derives
    assert _read(override).default_updated
    with system_context(reason="tests.messaging_templates.facts"):
        facts = MessageTemplate.objects.with_row_facts().get(pk=override.pk)
    assert (facts.is_effective, facts.is_overridden, facts.default_updated) == (True, False, True)


# ---------- override, keep, reset ----------


def test_override_copies_the_default_stamps_it_and_returns_the_same_row_again(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-overrider")
    baseline = _row("template", "tests.welcome", baseline=True, preheader="Read me", variables={"name": "Who"},
                    sample={"name": "Ana"}, description="Welcome mail")
    _row(baseline=True)  # the layout
    with actor_context(admin):
        source = MessageTemplate.objects.get(pk=baseline.pk)
        override = MessageTemplate.objects.override(source)
        again = MessageTemplate.objects.override(source)
    assert again.pk == override.pk
    assert not override.is_baseline
    assert (override.kind, override.key, override.language) == (baseline.kind, baseline.key, baseline.language)
    assert {name: getattr(override, name) for name in baseline.override_values()} == baseline.override_values()
    assert override.baseline_hash == baseline.content_hash
    assert override.content_hash == baseline.content_hash
    with system_context(reason="tests.messaging_templates.facts"):
        assert not MessageTemplate.objects.with_row_facts().get(pk=baseline.pk).is_effective
        assert MessageTemplate.objects.with_row_facts().get(pk=baseline.pk).is_overridden


def test_override_refuses_a_row_that_is_not_a_default_and_an_actor_who_may_not_create(db: None) -> None:
    del db
    member = create_user("messaging-templates-would-be-overrider")
    baseline = _row(baseline=True)
    override = _row()
    own = _row(key="letter")
    with system_context(reason="tests.messaging_templates.refuse"):
        for row in (override, own):
            with pytest.raises(ValidationError) as refused:
                MessageTemplate.objects.override(row)
            assert refused.value.messages == [NOT_A_BASELINE_MESSAGE]
    with system_context(reason="tests.messaging_templates.reset"):
        override.delete()
    with actor_context(member), pytest.raises(PermissionDenied), transaction.atomic():
        MessageTemplate.objects.override(MessageTemplate.objects.get(pk=baseline.pk))
    with system_context(reason="tests.messaging_templates.read"):
        assert MessageTemplate.objects.filter(is_baseline=False).count() == 1  # only the own row


def test_keep_override_clears_default_updated_and_refuses_a_row_with_no_default(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-keeper")
    baseline = _row(baseline=True)
    override = _row(source="<main>Ours</main>")
    own = _row(key="letter")
    assert override.baseline_hash == baseline.content_hash
    with system_context(reason="tests.messaging_templates.release"):
        baseline.source = "<main>Released</main>"
        baseline.save()
    assert _read(override).default_updated
    with actor_context(admin):
        kept = MessageTemplate.objects.keep_override(MessageTemplate.objects.get(pk=override.pk))
    assert kept.baseline_hash == _read(baseline).content_hash
    assert not _read(override).default_updated
    assert _read(override).source == "<main>Ours</main>"
    with system_context(reason="tests.messaging_templates.refuse"):
        for row in (baseline, own):
            with pytest.raises(ValidationError) as refused:
                MessageTemplate.objects.keep_override(row)
            assert refused.value.messages == [NO_BASELINE_MESSAGE]


def test_deleting_the_override_resets_to_the_default(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-resetter")
    baseline = _row(baseline=True)
    override = _row(source="<main>Ours</main>")
    assert result_data(execute_schema(_schema(), _DELETE, {"id": public_id_of(override)}, user=admin))
    with system_context(reason="tests.messaging_templates.read"):
        assert MessageTemplate.objects.effective("layout", "base") == baseline
        assert not MessageTemplate.objects.filter(pk=override.pk).exists()


_OVERRIDE = "mutation($id: ID!) { override_message_template(template: $id) { ok message id } }"
_KEEP = "mutation($id: ID!) { keep_message_template_override(template: $id) { ok message id } }"


def test_the_two_verbs_answer_with_an_action_result(db: None) -> None:
    del db
    admin = create_platform_admin("messaging-templates-verbs-admin")
    member = create_user("messaging-templates-verbs-member")
    baseline = _row(baseline=True)
    variables = {"id": public_id_of(baseline)}

    first = result_data(execute_schema(_schema(), _OVERRIDE, variables, user=admin))["override_message_template"]
    again = result_data(execute_schema(_schema(), _OVERRIDE, variables, user=admin))["override_message_template"]
    with system_context(reason="tests.messaging_templates.read"):
        override = MessageTemplate.objects.get(is_baseline=False)
    assert first == again == {"ok": True, "message": "Message template overridden.", "id": override.sqid}

    for refused in (member, AnonymousUser()):
        denied = execute_schema(_schema(), _OVERRIDE, variables, user=refused)
        assert denied.errors and denied.errors[0].extensions["code"] == "PERMISSION_DENIED"
    not_a_default = result_data(execute_schema(_schema(), _OVERRIDE, {"id": override.sqid}, user=admin))
    assert not_a_default["override_message_template"]["ok"] is False
    assert not_a_default["override_message_template"]["message"] == NOT_A_BASELINE_MESSAGE

    kept = result_data(execute_schema(_schema(), _KEEP, {"id": override.sqid}, user=admin))
    assert kept["keep_message_template_override"] == {"ok": True, "message": "Override kept.", "id": override.sqid}
    unwritable = result_data(execute_schema(_schema(), _KEEP, {"id": override.sqid}, user=member))
    assert unwritable["keep_message_template_override"]["ok"] is False
    no_default = result_data(execute_schema(_schema(), _KEEP, {"id": public_id_of(baseline)}, user=admin))
    assert no_default["keep_message_template_override"]["ok"] is False


# ---------- row facts ----------


_FACTS = """
query($where: message_templates_bool_exp) {
  message_templates(where: $where) { key language is_baseline is_effective is_overridden default_updated }
}
"""


def test_the_row_facts_read_as_fields_and_filter_the_list(db: None) -> None:
    del db
    member = create_user("messaging-templates-facts-reader")
    baseline = _row(baseline=True)
    _row(source="<main>Ours</main>")
    _row(language="es", baseline=True)
    _row(key="letter")
    with system_context(reason="tests.messaging_templates.release"):
        baseline.source = "<main>Released</main>"
        baseline.save()

    def listed(where: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        rows = result_data(execute_schema(_schema(), _FACTS, {"where": where}, user=member))["message_templates"]
        return sorted(tuple(row.values()) for row in rows)

    assert listed() == [
        ("base", "en", False, True, False, True),
        ("base", "en", True, False, True, False),
        ("base", "es", True, True, False, False),
        ("letter", "en", False, True, False, False),
    ]
    assert [row[:3] for row in listed({"is_effective": {"_eq": False}})] == [("base", "en", True)]
    assert [row[:3] for row in listed({"is_overridden": {"_eq": True}})] == [("base", "en", True)]
    assert [row[:3] for row in listed({"default_updated": {"_eq": True}})] == [("base", "en", False)]


def test_a_row_returned_without_annotations_reads_its_facts_itself(db: None) -> None:
    del db
    baseline = _row(baseline=True)
    override = _row()
    assert (baseline.is_effective, baseline.is_overridden, baseline.default_updated) == (False, True, False)
    assert (override.is_effective, override.is_overridden, override.default_updated) == (True, False, False)


def test_listing_rows_with_their_facts_costs_the_same_at_one_and_at_twenty_five_rows(db: None) -> None:
    del db
    member = create_user("messaging-templates-lister")

    def statements(rows: int) -> int:
        with CaptureQueriesContext(connection) as captured:
            listed = result_data(execute_schema(_schema(), _FACTS, {"where": None}, user=member))["message_templates"]
        assert len(listed) == rows
        return len(captured)

    _row(baseline=True)
    one = statements(1)
    for index in range(1, 25):
        _row(key=f"layout_{index}", baseline=index % 2 == 0)
    assert statements(25) == one


# ---------- languages ----------


def test_the_row_a_recipient_reads(db: None) -> None:
    """The language asked for, else the person's, then the default; an override before its baseline."""

    del db
    _row(baseline=True)
    override = _row(source="<main>Ours</main>")
    spanish = _row(language="es", baseline=True)
    german = _row(language="de")
    with system_context(reason="tests.messaging_templates.read"):
        rows = MessageTemplate.objects
        assert rows.effective("layout", "base", language="es") == spanish
        assert rows.effective("layout", "base", language="EN") == override
        assert rows.effective("layout", "base", language="it") == override
        assert rows.effective("layout", "base", person=Person(languages=["fr", "de", "es"])) == german
        assert rows.effective("layout", "base", person=Person(languages=[])) == override
        assert rows.effective("layout", "base", language="es", person=Person(languages=["de"])) == spanish
        assert rows.effective("layout", "base") == override


def test_many_keys_resolve_in_one_query(db: None) -> None:
    del db
    _row(baseline=True)
    _row("block", "button", baseline=True)
    _row("block", "button", language="es", baseline=True)
    _row("part", "greeting", language="es")
    table = MessageTemplate._meta.db_table
    with system_context(reason="tests.messaging_templates.read"), CaptureQueriesContext(connection) as captured:
        rows = MessageTemplate.objects.effective_rows(["layout", "block", "part"], language="es")
    assert sorted((kind, key, row.language) for (kind, key), row in rows.items()) == [
        ("block", "button", "es"), ("layout", "base", "en"), ("part", "greeting", "es"),
    ]
    assert len([query for query in captured.captured_queries if table in query["sql"]]) == 1
    with system_context(reason="tests.messaging_templates.read"):
        found = MessageTemplate.objects.effective_rows(["block"], keys=["button", "missing"])
    assert set(found) == {("block", "button")}


@override_settings(LANGUAGE_CODE="pt-br")
def test_a_key_no_tried_language_has_falls_back_to_a_shipped_language_english_first(db: None) -> None:
    """With a Portuguese default, the shipped English row and its override still render; else the first by code."""

    del db
    _row(baseline=True)
    override = _row()
    french = _row("part", "greeting", language="fr", baseline=True)
    _row("part", "greeting", language="es", baseline=True)
    _row("part", "greeting", language="de")  # the deployment's own row in a language nobody asked for
    with system_context(reason="tests.messaging_templates.read"):
        assert MessageTemplate.objects.language_candidates(language="it") == ["it", "pt"]
        assert MessageTemplate.objects.effective("layout", "base", language="it") == override
        greeting = MessageTemplate.objects.effective("part", "greeting", language="it")
    assert greeting.language == "es" and greeting.is_baseline
    assert french.language == "fr"


def test_a_key_with_no_row_raises_naming_it_and_the_languages_tried(db: None) -> None:
    del db
    _row("part", "greeting", language="de")
    with system_context(reason="tests.messaging_templates.read"):
        with pytest.raises(MessageTemplate.DoesNotExist, match=r"part 'greeting' in es, en"):
            MessageTemplate.objects.effective("part", "greeting", language="es")
        with pytest.raises(ValidationError):
            MessageTemplate.objects.effective("part", "greeting", language="english")


def test_a_key_resolves_with_a_default_in_any_language_or_a_row_in_the_default_language(db: None) -> None:
    del db
    _row(key="shipped", language="fr", baseline=True)
    _row(key="own", language="en")
    _row(key="translated", language="de")
    with system_context(reason="tests.messaging_templates.read"):
        rows = MessageTemplate.objects
        assert rows.resolves("layout", "shipped")
        assert rows.resolves("layout", "own")
        assert not rows.resolves("layout", "translated")
        assert not rows.resolves("layout", "missing")
        assert not rows.resolves("block", "shipped")


# ---------- deleting ----------


def test_a_layout_templates_use_cannot_be_deleted_while_its_key_would_stop_resolving(db: None) -> None:
    """Instance, queryset, cascade and console deletes refuse; a translation whose default-language row remains goes."""

    del db
    admin = create_platform_admin("messaging-templates-deleter")
    layout = _row(key="letter")
    translation = _row(key="letter", language="de")
    unused = _row(key="unused")
    _row("template", "tests.welcome", layout="letter")
    with system_context(reason="tests.messaging_templates.delete"):
        assert layout.delete_blocker() == (
            "Other message templates use this layout, which would no longer resolve without this row: "
            "give its key a row in the default language first."
        )
        with pytest.raises(ValidationError), transaction.atomic():
            MessageTemplate.objects.get(pk=layout.pk).delete()
        with pytest.raises(ValidationError), transaction.atomic():
            MessageTemplate.objects.filter(pk=layout.pk).delete()
        cascade = Collector(using="default", origin=admin)
        cascade.collect([MessageTemplate.objects.get(pk=layout.pk)])
        with pytest.raises(ValidationError), transaction.atomic():
            cascade.delete()
    console = execute_schema(_schema(), _DELETE, {"id": public_id_of(layout)}, user=admin)
    assert console.errors
    with system_context(reason="tests.messaging_templates.delete"):
        translation.delete()
        unused.delete()
        assert MessageTemplate.objects.filter(pk=layout.pk).exists()


def test_an_overridden_layout_goes_back_to_its_default_when_the_override_is_deleted(db: None) -> None:
    del db
    _row(baseline=True)
    override = _row()
    _row("template", "tests.welcome")
    with system_context(reason="tests.messaging_templates.delete"):
        assert override.delete_blocker() is None
        override.delete()
