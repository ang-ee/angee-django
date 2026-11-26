"""Every module that reaches the system channel's one writer is named, with the permission that decides it.

``MessageManager.compose_as_system`` writes system mail as named system work and
consults no send right: the caller's own permission decided the send. So each
module under ``src`` and ``examples`` that refers to it is listed in ``CALLERS``
with that permission; a new caller adds its module and says what decides it.

The scan counts any reference to the name: an attribute (``manager.compose_as_system``,
called, bound to a variable or passed on), a name imported or assigned, and the
string handed to ``getattr``. It cannot see a name built at runtime, nor the
callers of a listed module's own entry point: every caller of Django's mail API
(``send_mail``, ``EmailMessage.send``, ``mail_admins``) reaches the writer through
the system mailer, which ``MAILERS["default"]`` selects.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WRITER = "compose_as_system"
CALLERS = {
    # The system mailer, Django's default mailer: each email Django sends is
    # recorded. The code that calls Django's mail API decided that send.
    Path("src/angee/messaging/email.py"),
}


def _references(tree: ast.Module) -> list[int]:
    """Return the lines of every reference to the writer in ``tree``; its own ``def`` is none."""

    return sorted(
        node.lineno for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == WRITER)
        or (isinstance(node, ast.Name) and node.id == WRITER)
        or (isinstance(node, ast.Constant) and node.value == WRITER)
        or (isinstance(node, ast.alias) and WRITER in (node.name, node.asname))
    )


def _callers() -> dict[Path, list[int]]:
    found = {}
    for top in ("src", "examples"):
        for path in sorted((ROOT / top).rglob("*.py")):
            lines = _references(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            if lines:
                found[path.relative_to(ROOT)] = lines
    return found


def test_every_caller_of_the_system_mail_writer_is_named_with_its_permission() -> None:
    found = _callers()
    unnamed = {str(path): lines for path, lines in found.items() if path not in CALLERS}
    assert unnamed == {}, "Name each caller of compose_as_system in CALLERS, with the permission that decides it."
    assert sorted(str(path) for path in CALLERS - set(found)) == [], "A listed caller no longer refers to the writer."


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
    assert _references(ast.parse(source)) != []


def test_a_mention_in_prose_or_the_owners_definition_is_no_reference() -> None:
    source = 'def compose_as_system(self):\n    """Write; see :meth:`compose_as_system`."""\n'
    assert _references(ast.parse(source)) == []
