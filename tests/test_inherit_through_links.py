"""The framework under ``REBAC_INHERIT_THROUGH_LINKS = False`` fails closed by structure.

- An elevated read names the policy arm it relies on (``PolicyArm``) by the identity the library's
  ``rebac.links.narrowed_arrows()`` reports, and elevates only while the policy REBAC decides with states
  that arm unchanged; in a read operation a framework queryset elevated without one refuses to read.
- The setting refuses to be read ``False`` while an installed app has not declared it follows the policy.
- Every reach of a search, filter or ordering into another model's columns is enumerated here and decided,
  so a new one fails until it is.
"""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, system_context
from rebac.backends import backend
from rebac.links import narrowed_arrows
from rebac.schema import PermArrow, PermBinOp, Permission, PermRef
from rebac.testing import install_schema

import angee
from angee.base import permissions
from angee.base.apps import refuse_undeclared_narrowed_policy
from angee.base.permissions import (
    POLICY_ARMS,
    SHOWN_FACTS,
    PolicyArm,
    arm_holds,
    elevated_read,
    links_narrowed,
    read_operation,
    undeclared_policy_apps,
)
from angee.graphql.introspection import FieldPathError, fields_for_path
from angee.graphql.schema import GraphQLSchemas
from angee.messaging.mixins import MESSAGES_READ
from angee.messaging.testing.models import Message, ThreadAttachment
from angee.projects.testing.models import Project, Task
from tests.conftest import RecordBinding
from tests.messaging_campaign import grant
from tests.t3_campaign import campaign_access as campaign_access  # noqa: F401
from tests.t3_campaign import campaign_user as campaign_user  # noqa: F401

TASK_CHATTER = PolicyArm("messaging/thread", "chatter_read", "record_task", "read")
"""A task's readers read its chatter thread: an arrow through a generic relation, a link."""


# ---------- The arms the framework relies on ----------


def test_every_declared_arm_is_stated_and_holds_as_the_library_reports(db: None) -> None:
    """Each arm the framework names is written in the published policy; it holds unless the library narrows it."""

    del db
    GraphQLSchemas.from_discovery().build("console")  # imports every resolver module
    published = backend().schema()
    reported = {(arrow.resource_type, arrow.permission, arrow.relation, arrow.target) for arrow in narrowed_arrows()}
    for arm in sorted(POLICY_ARMS - {TASK_CHATTER}, key=str):
        definition = published.get_definition(arm.resource_type)
        stated = next((p for p in definition.permissions if p.name == arm.permission), None) if definition else None
        assert stated is not None and _states(stated.expression, arm), f"{arm} is not stated by the policy"
        with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
            narrowed = (arm.resource_type, arm.permission, arm.relation, arm.target) in reported
            assert arm_holds(arm) is not narrowed, arm


def test_every_shown_fact_names_a_type_the_policy_defines(db: None) -> None:
    """Facts shown beyond a row's read are declared once each, about a record type the policy has."""

    del db
    GraphQLSchemas.from_discovery().build("console")
    published = {definition.resource_type for definition in backend().schema().definitions}
    assert SHOWN_FACTS and {fact.resource_type for fact in SHOWN_FACTS} <= published


def _states(expression: Any, arm: PolicyArm) -> bool:
    if isinstance(expression, PermBinOp):
        return _states(expression.left, arm) or _states(expression.right, arm)
    if arm.target:
        return isinstance(expression, PermArrow) and (expression.via, expression.target) == (arm.relation, arm.target)
    return isinstance(expression, PermRef) and expression.name == arm.relation


def test_an_arm_holds_only_where_the_evaluated_policy_states_it_unchanged(db: None) -> None:
    """A narrowed arrow, an arm spelled through another permission and an arm under an exclusion hold no more."""

    del db
    with CaptureQueriesContext(connection) as captured, override_settings(REBAC_INHERIT_THROUGH_LINKS=True):
        assert arm_holds(TASK_CHATTER) and arm_holds(MESSAGES_READ)
    assert captured.captured_queries == []
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
        # The library lends a task's chatter only the task's own grants: the arrow is renamed.
        assert not arm_holds(TASK_CHATTER)
        # A parent arrow is never narrowed.
        assert arm_holds(MESSAGES_READ)
        assert not arm_holds(PolicyArm("messaging/thread", "chatter_read", "record_nothing", "read"))
    published = backend().schema()
    install_schema(_respelled(published, PermArrow("record_task", "chatter_view")), backend=backend())
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
        # The arm written through another permission of the task is not the arm the code names.
        assert not arm_holds(TASK_CHATTER)
    install_schema(_respelled(published, PermBinOp("-", PermRef("reader"), PermArrow("record_task", "read"))),
                   backend=backend())
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
        # Under the right side of an exclusion the arm takes access away; it lends none.
        assert not arm_holds(PolicyArm("messaging/thread", "chatter_read", "record_task", "read"))


def _respelled(published: Any, task_arm: Any) -> Any:
    """The policy with the task's chatter arm replaced by ``task_arm`` (and the task's ``chatter_view = read``)."""

    def respell(expression: Any) -> Any:
        if isinstance(expression, PermArrow) and expression.via == "record_task":
            return task_arm
        if isinstance(expression, PermBinOp):
            return PermBinOp(expression.op, respell(expression.left), respell(expression.right))
        return expression

    definitions = []
    for definition in published.definitions:
        if definition.resource_type == "messaging/thread":
            definition = replace(definition, permissions=tuple(
                replace(permission, expression=respell(permission.expression))
                if permission.name == "chatter_read" else permission
                for permission in definition.permissions
            ))
        elif definition.resource_type == "projects/task":
            definition = replace(
                definition, permissions=(*definition.permissions, Permission("chatter_view", PermRef("read"))),
            )
        definitions.append(definition)
    return replace(published, definitions=definitions)


RECORD_FEED = """
query RecordFeed($model: String!, $id: ID!) {
  record_thread(input: {model_label: $model, record_id: $id}) { error_code messages { id preview } }
}
"""


@pytest.mark.parametrize("aliased", [False, True])
def test_a_chatter_arm_spelled_through_another_permission_lends_no_feed(
    campaign_access: Any, campaign_user: Any, aliased: bool,
) -> None:
    """The record feed asks REBAC for the thread's read, so how the policy spells the arm changes nothing.

    The second review's case: the task's chatter arm written through an alias permission of the task. A
    project reader reads the task through its project; under ``False`` the feed refuses them either way.
    """

    from angee.base.models.identity import public_id_for
    from angee.graphql.testing.documents import execute_schema, result_data, unreadable_rows
    from tests.test_messaging_graphql import _schema as messaging_console_schema

    owner, reader = campaign_user("alias-owner"), campaign_user("alias-reader")
    if aliased:
        install_schema(_respelled(backend().schema(), PermArrow("record_task", "chatter_view")), backend=backend())
    with system_context(reason="tests.links.alias"):
        project = Project.objects.create(title="Alias project")
        task = Task.objects.create(project=project, owner=owner)
        thread = ThreadAttachment.objects.ensure_for_record(task).thread
        Message._base_manager.create(created_by=owner, thread=thread, message_type="comment", status="synced")
    grant(project, "reader", reader)
    variables = {"model": type(task)._meta.label, "id": public_id_for(type(task), task.pk)}
    schema = messaging_console_schema()
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
        assert task.with_actor(reader).has_access("read")
        assert not thread.with_actor(reader).has_access("read")
        feed = result_data(execute_schema(schema, RECORD_FEED, variables, user=reader))["record_thread"]
        assert (feed["error_code"], feed["messages"]) == ("PERMISSION_DENIED", [])
        assert unreadable_rows(schema, RECORD_FEED, variables, actor=reader) == []
        assert reader.pk not in task.thread_reader_ids([reader])


# ---------- Elevated reads in a read operation ----------


@pytest.mark.parametrize("inherits", [True, False])
def test_an_elevated_read_in_a_read_operation_names_an_arm_that_holds(
    campaign_access: Any, campaign_user: Any, inherits: bool,
) -> None:
    """Under ``False`` a framework queryset elevated without a named arm refuses to read inside a read operation.

    Its ``sudo()`` and ``system_context()`` mark it; ``elevated_read`` elevates only while its arm holds, and
    otherwise leaves REBAC to decide each row for the actor. Outside a read operation, and with the published
    policy, nothing changes.
    """

    owner, reader = campaign_user("net-owner"), campaign_user("net-reader")
    with system_context(reason="tests.links.net"):
        project = Project.objects.create(title="Net project")
        task = Task.objects.create(project=project, owner=owner)
        thread = ThreadAttachment.objects.ensure_for_record(task).thread
        Message._base_manager.create(created_by=owner, thread=thread, message_type="comment", status="synced")
    grant(project, "reader", reader)
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits), actor_context(reader):
        unnamed = Message.objects.sudo(reason="tests.links.unnamed").filter(thread=thread)
        assert unnamed.count() == 1
        with read_operation():
            reads = (
                list,
                lambda rows: rows.count(),
                lambda rows: rows.exists(),
                lambda rows: list(rows.iterator()),
                lambda rows: rows.system_context(reason="tests.links.unnamed").first(),
            )
            for read in reads:
                if inherits:
                    read(unnamed)
                else:
                    with pytest.raises(PermissionDenied, match="names no policy arm"):
                        read(unnamed)
            named = elevated_read(Message.objects.filter(thread=thread), MESSAGES_READ, reason="tests.links.named")
            assert named.count() == 1
            # Where the arm does not hold, REBAC decides: the project reader reads no task chatter.
            lent = elevated_read(Message.objects.filter(thread=thread), TASK_CHATTER, reason="tests.links.lent")
            assert lent.count() == (1 if inherits else 0)


_TIMELINE_PROBE = (
    "a timeline kind's existence, record identity or waiting predicate (TimelineKind): it decides which "
    "authorized branch to read and shows no row"
)
_MUTATION = "a mutation: a write the verb authorized; its payload reads through the same types"
_ADMIN = "an IAM or platform administration read behind the administrator's own permission"
_HISTORY = "record history (provenance RECORD_HISTORY): the record's own versions, behind the record's read"

RESOLVER_ELEVATIONS: dict[tuple[str, str], str] = {
    ("calendar/schema.py", "filter"): "change-stream bookkeeping: maps an event to its stored row; ChangeReadGate "
    "decides the delivery",
    ("checklists/timeline.py", "occurs"): _TIMELINE_PROBE,
    ("decisions/timeline.py", "occurs"): _TIMELINE_PROBE,
    ("decisions/timeline.py", "waiting_on"): _TIMELINE_PROBE,
    ("messaging/timeline.py", "occurs"): _TIMELINE_PROBE,
    ("messaging/timeline.py", "targets"): _TIMELINE_PROBE,
    ("nexus/timeline.py", "_record_filter"): _TIMELINE_PROBE,
    ("nexus/timeline.py", "occurs"): _TIMELINE_PROBE,
    ("nexus/timeline.py", "records"): _TIMELINE_PROBE,
    ("projects/timeline.py", "occurs"): _TIMELINE_PROBE,
    ("projects/timeline.py", "waiting_on"): _TIMELINE_PROBE,
    ("provenance/timeline.py", "occurs"): _TIMELINE_PROBE,
    ("workflows/timeline.py", "occurs"): _TIMELINE_PROBE,
    ("workflows/timeline.py", "waiting_on"): _TIMELINE_PROBE,
    ("workflows/timeline.py", "open_decision_count"): _TIMELINE_PROBE,
    ("iam/schema.py", "_grant_rows_for"): _ADMIN,
    ("iam/schema.py", "_role_rows_for"): _ADMIN,
    ("iam/schema.py", "bindings"): _ADMIN,
    ("iam/schema.py", "iam_principal_access"): _ADMIN,
    ("iam/schema.py", "members"): _ADMIN,
    ("platform/schema.py", "addon_change_preview"): _ADMIN,
    ("iam/schema.py", "grant_role"): _MUTATION,
    ("iam/schema.py", "revoke_role"): _MUTATION,
    ("iam/schema.py", "login"): _MUTATION,
    ("iam/schema.py", "redeem_sign_in_link"): _MUTATION,
    ("iam_integrate_oidc/schema.py", "login_complete"): _MUTATION,
    ("integrate/schema.py", "update"): _MUTATION,
    ("platform/schema.py", "install"): _MUTATION,
    ("platform/schema.py", "uninstall"): _MUTATION,
    ("provenance/schema.py", "revert_changeset"): _MUTATION,
    ("storage/schema.py", "purge_file"): _MUTATION,
    ("messaging/schema.py", "filter"): "the notification change gate loads the delivery row a change names, to ask "
    "REBAC for its message's read",
    ("knowledge/schema.py", "vault_label"): "a page's vault name beside the page, as the list's annotation shows it "
    "(main's rule: the name, without vault access); a mutation payload reads it here",
    ("messaging/inbox.py", "involving_handles"): "merge redirect groups of handles (ids), composed into the reader's "
    "scoped statement",
    ("messaging/inbox_results.py", "bucket_rows"): "merge redirect groups (ids), composed into the reader's scoped "
    "statement",
    ("provenance/schema.py", "_merges"): _HISTORY,
    ("provenance/schema.py", "_redactions"): _HISTORY,
    ("provenance/schema.py", "_versions"): _HISTORY,
    ("work/schema.py", "project_manager_roster"): "the project's team moderators and their labels, behind the "
    "project's read, each seat's identity only where its reader reads the seat (with_can_read)",
}
"""Every ambient elevation (``system_context(...)``, ``sudo(...)``, a base manager) in a GraphQL resolver module.

The read-operation refusal sees a framework queryset's own ``sudo()`` and ``system_context()``, not an
ambient block or a base manager; here each of those, by module and function, is decided. A new one fails
until it is named (``elevated``, ``elevated_read``) or decided here.
"""

_RESOLVER_MODULES = re.compile(r"(^|/)(schema|[a-z_]+_schema|timeline|reading|inbox[a-z_]*)\.py$")


def test_every_ambient_elevation_in_a_resolver_module_is_decided() -> None:
    root = Path(angee.__path__[0])
    found: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if not _RESOLVER_MODULES.search(relative) or "/testing/" in relative or "/tests/" in relative:
            continue
        stack: list[str] = []

        class _Elevations(ast.NodeVisitor):
            def visit_FunctionDef(self, node: Any) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id in ("system_context", "sudo"):
                    found.add((relative, stack[-1] if stack else "<module>"))
                self.generic_visit(node)

            def visit_Attribute(self, node: ast.Attribute) -> None:
                if node.attr == "_base_manager":
                    found.add((relative, stack[-1] if stack else "<module>"))
                self.generic_visit(node)

        _Elevations().visit(ast.parse(path.read_text(encoding="utf-8")))
    assert sorted(found) == sorted(RESOLVER_ELEVATIONS)


# ---------- Rules that copied an arrow ----------


@pytest.mark.parametrize("inherits", [True, False])
def test_binding_knowledge_to_a_record_is_offered_as_the_binding_write_decides(
    campaign_access: Any, campaign_user: Any, inherits: bool,
) -> None:
    """A project editor writes a task through its project; under ``False`` the binding's target write is not theirs."""

    owner, editor = campaign_user("bind-owner"), campaign_user("bind-editor")
    with system_context(reason="tests.links.bind"):
        project = Project.objects.create(title="Bind project")
        task = Task.objects.create(project=project, owner=owner)
    grant(project, "editor", editor)
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits), actor_context(editor):
        assert task.with_actor(editor).has_access("write")
        assert RecordBinding._default_manager.target_writable(task.with_actor(editor)) is inherits
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=False), actor_context(owner):
        assert RecordBinding._default_manager.target_writable(task.with_actor(owner))


# ---------- The refusal of the setting ----------


def test_every_installed_app_declares_its_reads_follow_the_evaluated_policy() -> None:
    """The framework vouches for what it ships, test apps binding a policy included."""

    assert undeclared_policy_apps() == ()
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=False):
        assert links_narrowed()


@pytest.mark.parametrize("inherits", [True, False, "False"])
def test_an_undeclared_app_refuses_the_setting_where_it_is_read(monkeypatch: pytest.MonkeyPatch, inherits: Any) -> None:
    """``angee.E055`` holds at start-up in every process and at each framework reading, not as a system check.

    A value that is not a bool is REBAC's own ``rebac.E028``: the start-up refusal leaves it to REBAC, whose
    every decision refuses it.
    """

    monkeypatch.setattr(permissions, "undeclared_policy_apps", lambda: ("an.undeclared.app",))
    with override_settings(REBAC_INHERIT_THROUGH_LINKS=inherits):
        if inherits is False:
            with pytest.raises(ImproperlyConfigured, match=r"an\.undeclared\.app \(angee\.E055\)"):
                refuse_undeclared_narrowed_policy()
            with pytest.raises(ImproperlyConfigured, match="angee.E055"):
                links_narrowed()
        else:
            refuse_undeclared_narrowed_policy()


# ---------- Reaches into another model's columns ----------

SEARCH_REACHES: dict[tuple[str, str], str] = {
    ("messaging/managers.py", "parts__file__title__icontains"): (
        "an attachment's file: under False only files the reader reads (_message_search_query); with the "
        "published policy main's join, which reaches a file an exclusion hides"
    ),
    ("messaging/managers.py", "parts__file__filename__icontains"): "as parts__file__title__icontains",
    ("messaging/managers.py", "parts__name__icontains"): "the message's own part (a foreign key of the row's child)",
    ("messaging/managers.py", "parts__fragment__text__icontains"): "the part's text fragment (no REBAC row)",
    ("messaging/managers.py", "subtype__name__icontains"): "the subtype vocabulary (no REBAC row)",
    ("messaging/managers.py", "subtype__description__icontains"): "the subtype vocabulary (no REBAC row)",
    ("messaging/managers.py", "fragment__text__iexact"): "a text fragment (no REBAC row)",
    ("messaging/inbox.py", "fragment__text__icontains"): "a text fragment (no REBAC row)",
    ("messaging/inbox_navigator.py", "title__text__icontains"): "a thread's title fragment (no REBAC row)",
    ("messaging/inbox_navigator.py", "circle__path__startswith"): "the reader's own circle hierarchy path",
    ("knowledge/retrieval.py", "markdown__body__icontains"): "the page's own markdown body (its parent link)",
    ("portfolio/models.py", "initiative__path__startswith"): "an initiative's materialized path (ids, no text)",
}
"""Every text lookup in the framework that crosses a relation, and why its reach is safe or how it is scoped."""

_TEXT_LOOKUP = re.compile(r"^[a-z_]+(?:__[a-z_]+)+__(?:icontains|contains|istartswith|startswith|iexact|search)$")


def test_every_text_lookup_across_a_relation_is_decided() -> None:
    """A search predicate that matches another model's columns must be scoped by REBAC or shown harmless here."""

    root = Path(angee.__path__[0])
    found = set()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if "/testing/" in relative or "/tests/" in relative or "/migrations/" in relative:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _TEXT_LOOKUP.match(node.value):
                found.add((relative, node.value))
            elif isinstance(node, ast.keyword) and node.arg and _TEXT_LOOKUP.match(node.arg):
                found.add((relative, node.arg))
    assert sorted(found) == sorted(SEARCH_REACHES)


FILTER_EXPRESSIONS: dict[str, str] = {
    "has_open_decisions": (
        "a flag on the record: an open decision is attached (decisions' attention rule, which tells a record's "
        "readers whatever they may read of the question); unchanged by the switch, the owner's to rule on"
    ),
    "filer_name": "a model alias through the request's party, redacted to NULL where the party is unreadable",
    "record_id": "the row's own generic reference column",
    "record_model": "the row's own generic reference content type",
    "subject_id": "the row's own generic reference column",
    "subject_model": "the row's own generic reference content type",
    "model_label": "the row's own generic reference content type",
    "writable": "the row's own permission, decided by REBAC (permission_expression)",
    "directory": "the row's own permission, decided by REBAC (permission_expression)",
    "can_act": "the row's own permission, decided by REBAC (permission_expression)",
    "can_start": "the row's own permission, decided by REBAC (permission_expression)",
    "active": "the row's own field, through its field gate (readable)",
    "assignees": "a to-many relation filter whose operands resolve through the target's REBAC read",
    "groups": "a to-many relation filter whose operands resolve through the target's REBAC read",
    "hold": "the run's own steps and the version it pins (its parent rows)",
    "is_incident": "the run's own steps and the version it pins (its parent rows)",
    "is_held": "the message's own columns",
    "metadata_mailbox": "the message's own metadata",
    "navigation_root": "the page's own hierarchy (rows of its own type)",
    "removed_root": "the page's own hierarchy (rows of its own type)",
    "in_template": "a flag about the task's own project, a foreign key of the row: a template's tasks are nobody's",
    "is_published": "the workflow's own column",
    "is_open": "the decision's own columns",
    "is_overdue": "the decision's own columns",
    "to_review": "a flag over the party's own statements, which the party's readers read",
    "is_system": "the queue's own columns",
    "accepts_manual_entry": "the queue's own columns",
}
"""Resource filters computed by an expression, by field name, to why their reach is safe or how it is scoped.

``test_every_resource_filter_into_another_model_is_scoped_or_decided`` lists every ``where`` field that is
none of the resource's own column or a to-one relation path (read through the related row's REBAC scope by
``actor_scoped_relation_expression``); each must be decided here.
"""


def test_every_resource_filter_into_another_model_is_scoped_or_decided(db: None) -> None:
    """Each ``where`` field of every model resource reads its own columns, a scoped relation, or is decided."""

    del db
    schemas = GraphQLSchemas.from_discovery()
    computed = set()
    for name in schemas.names():
        schema = schemas.build(name)
        for resource in getattr(schema, "angee_resources", ()):
            model = resource.model
            bool_exp = schema._schema.get_type(f"{resource.roots.list_name}_bool_exp") if model else None
            for field in getattr(bool_exp, "fields", {}):
                if field.startswith("_") or field == "id":
                    continue
                try:
                    fields_for_path(model, field)
                except FieldPathError:
                    computed.add(field)
    assert sorted(computed) == sorted(FILTER_EXPRESSIONS)

