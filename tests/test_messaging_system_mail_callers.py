"""Every module that calls a system mail writer directly is named, with the permission that decides it.

``MessageManager.compose_as_system`` writes system mail as named system work and
consults no send right: the caller's own permission decided the send. So each
production module under ``src`` and ``examples`` that refers to it is a key of ``CALLERS``,
mapped to that permission; a new caller adds its module and says what decides it.
The private writer both writers share, ``MessageManager._write_outgoing``, writes
with no grant consulted when given no actor, so only its own file refers to it
(``OWN_FILE``).

The scan counts any reference to either name: an attribute (``manager.compose_as_system``,
called, bound to a variable or passed on), a name imported or assigned, and the
string handed to ``getattr``. It still cannot see a name built at runtime, nor the
callers of a listed module's own entry point: every caller of Django's mail API
(``send_mail``, ``EmailMessage.send``, ``mail_admins``) reaches the writer through
the system mailer, which ``MAILERS["default"]`` selects.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.system_mail_references import caller_sources, reference_lines

WRITER = "compose_as_system"
CALLERS = {
    # The system mailer, Django's default mailer: each email Django sends is recorded.
    Path("src/angee/messaging/email.py"): "the calling code's own gate (see SystemMailer)",
    # Message templates' one send path (``MessageTemplateManager._render_and_record``), behind two verbs.
    Path("src/angee/messaging_templates/models.py"): (
        "send_test: the template's preview (an administrator's test send, decided before the system renders); "
        "send_as_system: its caller's own permission, each caller listed in SYSTEM_SEND_CALLERS "
        "(tests/test_messaging_templates_send.py)"
    ),
}
PRIVATE_WRITER = "_write_outgoing"
OWN_FILE = {
    # compose decides reply on its channel; compose_as_system is named system work.
    Path("src/angee/messaging/managers.py"): "the writer's own file (compose and compose_as_system)",
}


def _unlisted(name: str, allowed: Mapping[Path, str], modules: Mapping[Path, ast.Module]) -> dict[str, list[int]]:
    """Return each module outside ``allowed`` that refers to ``name``, with the lines."""

    return {
        str(path): lines for path, tree in modules.items()
        if path not in allowed and (lines := reference_lines(tree, name))
    }


@pytest.mark.shared_inputs
@pytest.mark.parametrize(("writer", "allowed"), [(WRITER, CALLERS), (PRIVATE_WRITER, OWN_FILE)])
def test_every_caller_of_a_system_mail_writer_is_named_with_its_permission(
    writer: str, allowed: Mapping[Path, str],
) -> None:
    unstated = [str(path) for path, permission in allowed.items()
                if not (isinstance(permission, str) and permission.strip())]
    assert unstated == [], "State the permission that decides each listed caller's send."
    modules = caller_sources()
    assert _unlisted(writer, allowed, modules) == {}, f"Name each caller of {writer}, with the permission deciding it."
    stale = sorted(str(path) for path in allowed if not reference_lines(modules[path], writer))
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
    assert reference_lines(ast.parse(source), WRITER) != []


def test_a_mention_in_prose_or_the_owners_definition_is_no_reference() -> None:
    source = 'def compose_as_system(self):\n    """Write; see :meth:`compose_as_system`."""\n'
    assert reference_lines(ast.parse(source), WRITER) == []
