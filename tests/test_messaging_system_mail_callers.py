"""Every module that calls a system mail writer directly is named, with the permission that decides it.

``MessageManager.compose_as_system`` writes system mail as named system work and
consults no send right: the caller's own permission decided the send. So each
production module under ``src`` and ``examples`` that refers to it is a key of ``CALLERS``,
mapped to that permission; a new caller adds its module and says what decides it.
The private writer both writers share, ``MessageManager._write_outgoing``, writes
with no grant consulted when given no actor, so only its own file refers to it
(``OWN_FILE``).

The template sender and its shared step have their own allowlists.

The scan counts any reference to a guarded name: an attribute (``manager.compose_as_system``,
called, bound to a variable or passed on), a name imported or assigned, and the
string handed to ``getattr``. It still cannot see a name built at runtime, nor the
callers of a listed module's own entry point: every caller of Django's mail API
(``send_mail``, ``EmailMessage.send``, ``mail_admins``) reaches the writer through
the system mailer, which ``MAILERS["default"]`` selects.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRITER = "compose_as_system"
CALLERS = {
    # The system mailer, Django's default mailer: each email Django sends is recorded.
    Path("src/angee/messaging/email.py"): "the calling code's own gate (see SystemMailer)",
    # Message templates' one send path (``MessageTemplateManager._render_and_record``), behind two verbs.
    Path("src/angee/messaging_templates/models.py"): (
        "send_test: the template's preview (an administrator's test send, decided before the system renders); "
        "send_as_system: its caller's own permission, each caller listed in SYSTEM_SEND_CALLERS"
    ),
}
PRIVATE_WRITER = "_write_outgoing"
OWN_FILE = {
    # compose decides reply on its channel; compose_as_system is named system work.
    Path("src/angee/messaging/managers.py"): "the writer's own file (compose and compose_as_system)",
}

SYSTEM_SEND_CALLERS: frozenset[Path] = frozenset({
    # Mails a question only to the people it is put to, each judged by decisions' own rule before the send:
    # ``act`` and ``read`` on the question (``Decision.objects.deciders``/``put_to``, ``administrators``).
    Path("src/angee/decisions_messaging/models.py"),
})
"""The source modules that call ``send_as_system``, each added here in a reviewed change.

Call it only from system work whose own gate decided the send: a person's request reaches it only through such a
gate, never from a resolver, an MCP tool or a view that hands it a person's choice of row, address or values
unchecked. It checks no permission and decides nothing about the values, the links in them or who may be mailed."""

SHARED_STEP_CALLERS: frozenset[Path] = frozenset({Path("src/angee/messaging_templates/models.py")})
"""The one module that may call the shared send step ``_render_and_record``, whose verbs ask the row's side first."""


@cache
def _caller_sources() -> Mapping[Path, ast.Module]:
    """Read source modules outside the addons' packaged tests."""

    return {
        path.relative_to(ROOT): ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for top in ("src", "examples") for path in sorted((ROOT / top).rglob("*.py"))
        if "tests" not in path.relative_to(ROOT).parts
    }


def _reference_lines(tree: ast.Module, name: str) -> list[int]:
    """List references to the writer name, excluding prose and its own definition."""

    return sorted(
        node.lineno for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == name)
        or (isinstance(node, ast.Name) and node.id == name)
        or (isinstance(node, ast.Constant) and node.value == name)
        or (isinstance(node, ast.alias) and name in (node.name, node.asname))
    )


def _unlisted(name: str, allowed: Mapping[Path, str], modules: Mapping[Path, ast.Module]) -> dict[str, list[int]]:
    """Return each module outside ``allowed`` that refers to ``name``, with the lines."""

    return {
        str(path): lines for path, tree in modules.items()
        if path not in allowed and (lines := _reference_lines(tree, name))
    }


@pytest.mark.parametrize(("writer", "allowed"), [(WRITER, CALLERS), (PRIVATE_WRITER, OWN_FILE)])
def test_every_caller_of_a_system_mail_writer_is_named_with_its_permission(
    writer: str, allowed: Mapping[Path, str],
) -> None:
    unstated = [str(path) for path, permission in allowed.items()
                if not (isinstance(permission, str) and permission.strip())]
    assert unstated == [], "State the permission that decides each listed caller's send."
    modules = _caller_sources()
    assert _unlisted(writer, allowed, modules) == {}, f"Name each caller of {writer}, with the permission deciding it."
    stale = sorted(str(path) for path in allowed if not _reference_lines(modules[path], writer))
    assert stale == [], "A listed caller no longer refers to the writer."


def test_an_unlisted_module_that_calls_the_private_writer_fails_the_guard() -> None:
    module = ast.parse("def send(outgoing):\n    return Message.objects._write_outgoing(outgoing, actor=None)\n")
    elsewhere = Path("src/angee/elsewhere/mail.py")
    assert _unlisted(PRIVATE_WRITER, OWN_FILE, {elsewhere: module}) == {str(elsewhere): [2]}


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("Message.objects.compose_as_system(reason='r')", id="attribute-call"),
        pytest.param("writer = Message.objects.compose_as_system\nwriter(reason='r')", id="variable"),
        pytest.param("partial(manager.compose_as_system, reason='r')()", id="passed-on"),
        pytest.param("getattr(Message.objects, 'compose_as_system')(reason='r')", id="getattr"),
        pytest.param("from angee.messaging import compose_as_system as send\nsend()", id="import-alias"),
    ],
)
def test_the_scan_sees_a_reference_however_it_is_spelled(source: str) -> None:
    assert _reference_lines(ast.parse(source), WRITER) != []


def test_a_mention_in_prose_or_the_owners_definition_is_no_reference() -> None:
    source = 'def compose_as_system(self):\n    """Write; see :meth:`compose_as_system`."""\n'
    assert _reference_lines(ast.parse(source), WRITER) == []


def test_only_the_listed_modules_call_the_system_send_or_its_shared_step() -> None:
    """List every production caller of ``send_as_system`` and ``_render_and_record``.

    Messaging allows the template addon to call its writer. Each template sender owns its permission decision.
    """

    sources = _caller_sources()
    for name, allowed in (("send_as_system", SYSTEM_SEND_CALLERS), ("_render_and_record", SHARED_STEP_CALLERS)):
        found = {module for module, tree in sources.items() if _reference_lines(tree, name)}
        assert found == allowed, name


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("MessageTemplate.objects._render_and_record(row)", id="attribute"),
        pytest.param("getattr(MessageTemplate.objects, '_render_and_record')(row)", id="getattr"),
        pytest.param("step = manager._render_and_record\nstep(row)", id="variable"),
    ],
)
def test_the_tripwire_sees_a_reference_however_it_is_spelled(source: str) -> None:
    assert _reference_lines(ast.parse(source), "_render_and_record")
    definition = ast.parse('def _render_and_record(self):\n    """See :meth:`_render_and_record`."""\n')
    assert not _reference_lines(definition, "_render_and_record")
