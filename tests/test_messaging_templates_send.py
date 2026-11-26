"""A message template sent on messaging's system channel: as named system work (``send_as_system``), and as an
administrator's test send (``send_test``)."""

from __future__ import annotations

import ast
import email.policy
from contextlib import nullcontext
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rebac import (
    PermissionDenied,
    actor_context,
    anonymous_actor,
    current_actor,
    is_sudo,
    system_context,
    to_subject_ref,
)
from rebac.backends import backend
from rebac.models import PermissionAuditEvent
from rebac.roles import revoke as revoke_role
from rebac.schema.ast import PermBinOp, PermRef

from angee.brandkit.testing.models import BrandKit
from angee.jobs.enqueue import celery_app
from angee.messaging.constants import DELIVER_MESSAGE_TASK
from angee.messaging.managers import ChannelManager, MessageManager
from angee.messaging.tasks import deliver_message
from angee.messaging.testing.models import Handle, Message, Part, Recipient
from angee.messaging_templates.testing.models import MessageTemplate
from angee.messaging_templates.walker import TemplateWalker
from angee.provenance.context import changeset_model
from angee.provenance.contrib.angee.exceptions import CreationKeyConflict
from tests.conftest import create_platform_admin, create_user
from tests.messaging_campaign import grant
from tests.test_messaging_compose import _refused
from tests.test_messaging_system_mail import LOCMEM, SENDER, _install
from tests.test_messaging_templates_render import ANGEE, _mail, _recording, _render, _row
from tests.test_messaging_templates_render import kit as kit
from tests.test_messaging_templates_render import plain as plain

ADDRESS = "reader@example.test"
REASON = "tests.messaging_templates.send"
TEST_SEND_REASON = "messaging_templates.message_template.send_test"
"""The reason a test send's system work is audited under."""
GREETING = {"name": "The recipient's name.", "title": "What the mail is about."}
"""The variables of :func:`_greeting`, a template reading a name in its body and a title in its subject."""


def _greeting(**values: Any) -> Any:
    return _mail("Hello {{ name }}.", GREETING, subject="{{ title }} from {{ brand.name }}", **values)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The delivery hand-offs published, recorded and never made."""

    tasks: list[str] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: tasks.append(name))
    return tasks


@pytest.fixture
def mailing(kit: Any, settings: Any, sent: list[str]) -> None:
    """A transport mailer, a valid default sender and the default kit; no system channel yet."""

    del kit, sent
    settings.MAILERS = {"transport": LOCMEM}
    settings.DEFAULT_FROM_EMAIL = SENDER


@pytest.fixture
def installed(mailing: None) -> Any:
    """The installed system channel, beside :func:`mailing`."""

    del mailing
    return _install()


@pytest.fixture
def system_channel(installed: Any, plain: Any) -> Any:
    """The installed system channel and the minimal layout the templates here sit in."""

    del plain
    return installed


def _send(row: Any, values: dict[str, Any], **arguments: Any) -> Any:
    return MessageTemplate.objects.send_as_system(row, **{"reason": REASON, "to": ADDRESS, "values": values,
                                                          **arguments})


def _stored(message: Any) -> tuple[str, str, str]:
    """The subject, plain text and HTML the message's stored parts carry, as its backend reads them to send."""

    with system_context(reason="tests.messaging_templates.send.read"):
        row: Any = Message._base_manager.select_related("channel").get(pk=message.pk)
        written = row.channel.backend.email_message(row)
    (html,) = [alternative.content for alternative in written.alternatives if alternative.mimetype == "text/html"]
    return written.subject, written.body, html


def _delivered(message: Any) -> EmailMessage:
    """Deliver ``message`` through the in-memory transport as the worker does, and return what it sent, parsed."""

    assert deliver_message(message._meta.label_lower, message.pk, message.external_id)["delivered"]
    return message_from_bytes(mail.outbox[-1].message().as_bytes(), policy=email.policy.default)


def _walks(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every walk, so a test can say nothing rendered."""

    walked: list[Any] = []
    _recording(monkeypatch, TemplateWalker, "walk", lambda walker, row, rows: walked.append(row))
    return walked


def _audits() -> list[tuple[str, str]]:
    return list(PermissionAuditEvent.objects.order_by("pk").values_list("kind", "reason"))


# ---------- what one call records and sends ----------


def test_each_call_records_one_queued_message_holding_the_render_and_hands_it_off_once(
    system_channel: Any, kit: Any, sent: list[str], django_capture_on_commit_callbacks: Any,
) -> None:
    row = _greeting()
    values = {"name": "Ana", "title": "Welcome"}
    rendered = _render(row, values, kit)
    messages = []
    for calls in (1, 2):
        with django_capture_on_commit_callbacks(execute=True):
            messages.append(_send(row, values))
        assert sent == [DELIVER_MESSAGE_TASK] * calls
    first, second = messages
    assert first.pk != second.pk and first.external_id != second.external_id
    assert first.actor() is None
    with system_context(reason="tests.messaging_templates.send.read"):
        stored = Message._base_manager.get(pk=first.pk)
        recipients = list(Recipient._base_manager.filter(message=stored).values_list("role", "handle__value"))
    assert (stored.channel_id, stored.status, stored.direction, stored.message_type) == (
        system_channel.pk, "queued", "outbound", "email",
    )
    assert recipients == [("to", ADDRESS)]
    assert _stored(first) == (rendered.subject, rendered.plain_text.strip(), rendered.html)  # compose trims text
    assert rendered.subject == "Welcome from Acme"


def test_on_the_wire_it_is_from_the_system_sender_to_the_address_its_text_first_and_its_html_utf8(
    system_channel: Any, kit: Any,
) -> None:
    del system_channel
    row = _mail("¿Qué tal, {{ name }}?", {"name": "The recipient's name."})
    rendered = _render(row, {"name": "Zoë"}, kit)
    wire = _delivered(_send(row, {"name": "Zoë"}))
    assert (str(wire["From"]), str(wire["To"])) == (SENDER, ADDRESS)
    assert wire.get_content_type() == "multipart/alternative"
    text, html = wire.iter_parts()
    assert (text.get_content_type(), html.get_content_type()) == ("text/plain", "text/html")
    assert html.get_content_charset() == "utf-8"
    # MIME ends each part with a line break; compose stores the text trimmed.
    assert (text.get_content(), html.get_content()) == (rendered.plain_text, rendered.html + "\n")


@pytest.mark.parametrize(
    ("title", "subject"),
    [
        pytest.param("=?utf-8?q?=E2=80=AE?=moc.evil", "= ?utf-8?q?=E2=80=AE?=moc.evil", id="encoded-override"),
        pytest.param("=?utf-8?q?=E2=80=AE b", "= ?utf-8?q?=E2=80=AE b", id="unterminated"),
        pytest.param("Hi =?utf-8?q?a=0D=0AX-Injected:_1?=", "Hi = ?utf-8?q?a=0D=0AX-Injected:_1?=", id="encoded-crlf"),
        pytest.param("2+2=?", "2+2=?", id="kept"),
    ],
)
def test_the_wire_subject_is_exactly_the_rendered_one_on_one_line(
    system_channel: Any, kit: Any, title: str, subject: str,
) -> None:
    """A value spelled as an encoded word reaches the wire as written: no override, no line break, no header."""

    del system_channel
    row = _greeting()
    rendered = _render(row, {"name": "Ana", "title": title}, kit)
    message = _send(row, {"name": "Ana", "title": title})
    wire = _delivered(message)
    assert str(wire["Subject"]) == _stored(message)[0] == rendered.subject == f"{subject} from Acme"
    assert "‮" not in str(wire["Subject"]) and "X-Injected" not in wire


def test_rendered_output_is_never_compiled_again(system_channel: Any, kit: Any) -> None:
    del system_channel, kit
    wire = _delivered(_send(_greeting(), {"name": "{{ brand.name }}", "title": "{% if 1 %}x{% endif %}"}))
    text, html = (part.get_content() for part in wire.iter_parts())
    assert "Hello {{ brand.name }}." in text and "Hello {{ brand.name }}." in html
    assert str(wire["Subject"]) == "{% if 1 %}x{% endif %} from Acme"


# ---------- what is refused, before anything is written ----------


def test_a_layout_block_or_overridden_default_is_refused_before_anything_renders_or_is_elevated(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del system_channel
    shipped = _greeting(key="tests.shipped", is_baseline=True)
    _greeting(key="tests.shipped")
    with system_context(reason="tests.messaging_templates.send.load"):
        refused = {
            "not_a_template": [MessageTemplate.objects.get(kind="layout"), _row("block", "card"), _row("part", "end")],
            "not_effective": [MessageTemplate._base_manager.get(pk=shipped.pk)],
        }
    walked, audits = _walks(monkeypatch), _audits()
    for code, rows in refused.items():
        for row in rows:
            with _refused(ValidationError) as refusal:
                _send(row, {"name": "Ana", "title": "Welcome"})
            assert refusal.value.code == code, row.kind
    assert (walked, _audits()) == ([], audits)


def test_a_render_refusal_reads_no_system_channel_and_leaves_the_bypass_entry_alone(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entering the system context writes its audit row at once; it stays when the render then refuses."""

    del system_channel
    looked_up: list[Any] = []
    _recording(monkeypatch, ChannelManager, "system_channel", lambda manager: looked_up.append(manager))
    row, audits = _greeting(), _audits()
    with _refused(ValidationError) as refusal:
        _send(row, {"title": "Welcome"})
    ((code, message),) = [(item.code, item.message) for item in refusal.value.error_list]
    assert code == "invalid_values" and message.startswith("name ")
    assert looked_up == []
    assert _audits() == [*audits, (PermissionAuditEvent.KIND_SUDO_BYPASS, REASON)]


def _paused(channel: Any) -> None:
    with system_context(reason="tests.messaging_templates.send.pause"):
        channel.pause()


@pytest.mark.parametrize(
    ("state", "to", "key", "code", "looked_up"),
    [
        pytest.param(None, "not an address", "to", "invalid", False, id="address"),
        pytest.param(None, "Two <a@example.test>, b@example.test", "to", "invalid", False, id="two-addresses"),
        pytest.param(None, "rëader@example.test", "to", "cannot_carry", False,
                     id="recipient-the-transport-cannot-carry"),
        pytest.param("unconfigured", ADDRESS, "channel", "OUTBOUND_EMAIL_NOT_CONFIGURED", False, id="unconfigured"),
        pytest.param("uncarried sender", ADDRESS, "channel", "cannot_carry", False,
                     id="sender-the-transport-cannot-carry"),
        pytest.param("missing", ADDRESS, "channel", "SYSTEM_CHANNEL_MISSING", True, id="missing"),
        pytest.param("paused", ADDRESS, "channel", "OUTBOUND_EMAIL_CHANNEL_MISSING", True, id="paused"),
    ],
)
def test_what_the_system_channel_refuses_comes_back_under_its_argument_with_nothing_written(
    mailing: None, plain: Any, settings: Any, sent: list[str], django_capture_on_commit_callbacks: Any,
    state: str | None, to: str, key: str, code: str, looked_up: bool,
) -> None:
    """Nothing written, and nothing handed to delivery: the on-commit callbacks run, and none was registered.

    In ``compose_as_system``'s own order, after the render: its argument checks and an address the transport cannot
    carry, then system mail not configured, from settings, none of which reads the system channel; then, after the
    lookup's own audit row, the missing channel and a channel not connected. The render's audit row comes first.
    """

    del mailing, plain
    if state != "missing":
        channel = _install()
        if state == "paused":
            _paused(channel)
    if state == "unconfigured":
        settings.MAILERS = {}
    if state == "uncarried sender":
        settings.DEFAULT_FROM_EMAIL = "Angee <nöreply@example.test>"
    row, audits = _greeting(), _audits()
    with django_capture_on_commit_callbacks(execute=True) as callbacks, _refused(ValidationError, key=key, code=code):
        _send(row, {"name": "Ana", "title": "Welcome"}, to=to)
    assert (callbacks, sent) == ([], [])
    reasons = [reason for _, reason in _audits()[len(audits):]]
    assert reasons[0] == REASON, reasons
    assert ("messaging.system_channel" in reasons) is looked_up, reasons


def test_a_blank_reason_is_refused_before_anything_is_read(system_channel: Any) -> None:
    del system_channel
    row = _greeting()
    for reason in ("", "   "):
        with CaptureQueriesContext(connection) as queries, pytest.raises(ValueError, match="named reason"):
            _send(row, {"name": "Ana", "title": "Welcome"}, reason=reason)
        assert queries.captured_queries == []


# ---------- under which context it renders and records ----------


@pytest.mark.parametrize("caller", ["no actor", "anonymous"])
def test_without_a_signed_in_caller_the_documented_recipe_renders_and_records_under_one_reason(
    system_channel: Any, caller: str,
) -> None:
    """No actor, or the anonymous subject an unauthenticated request carries: the render still reads every row.

    The recipe the verb documents, run as written: the lookup inside the caller's own named work, then the send,
    so the template rows are elevated under the caller's reason alone, never the library's own.
    """

    del system_channel
    key, audits = _greeting().key, _audits()
    with nullcontext() if caller == "no actor" else actor_context(anonymous_actor()):
        with system_context(reason=REASON):
            row = MessageTemplate.objects.effective("template", key, person=None)
        assert (current_actor() is None) is (caller == "no actor")
        message = _send(row, {"name": "Ana", "title": "Welcome"})
    assert _stored(message)[0] == "Welcome from Acme"
    with system_context(reason="tests.messaging_templates.send.read"):
        assert Message._base_manager.get(pk=message.pk).created_by_id is None
    written = _audits()[len(audits):]
    assert written[:2] == [(PermissionAuditEvent.KIND_SUDO_BYPASS, REASON)] * 2
    assert not any(reason.startswith("messaging_templates.") for _, reason in written), written


def test_a_signed_in_caller_is_the_creator_of_what_it_causes_and_reads_none_of_it(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bypass is the render's alone: the message is recorded in the caller's context, which reads nothing."""

    del system_channel
    member = create_user("messaging-templates-send-cause")
    row = _loaded(member)
    elevated: list[bool] = []
    _recording(monkeypatch, MessageManager, "compose_as_system", lambda manager, **values: elevated.append(is_sudo()))
    with actor_context(member):
        message = _send(row, {"name": "Ana", "title": "Welcome"}, to="new-reader@example.test")
    assert elevated == [False]
    with system_context(reason="tests.messaging_templates.send.read"):
        stored: Any = Message._base_manager.select_related("thread").get(pk=message.pk)
        parts = list(Part._base_manager.filter(message=stored))
        recipients = list(Recipient._base_manager.filter(message=stored))
        handle = Handle._base_manager.get(value="new-reader@example.test")
    assert (stored.created_by_id, stored.thread.created_by_id, handle.created_by_id) == (member.pk, member.pk, None)
    targets: list[Any] = [stored, stored.thread, *parts, *recipients, handle]
    for target in targets:
        assert not target.with_actor(member).has_access("read"), target


def _loaded(reader: Any) -> Any:
    """The greeting template as ``reader`` loads it: through its own scope, which redacts the sample."""

    key = _greeting().key
    return MessageTemplate.objects.with_actor(reader).get(kind="template", key=key)


def test_a_row_any_reader_loaded_sends_what_the_system_loaded_row_sends(system_channel: Any) -> None:
    del system_channel
    member = create_user("messaging-templates-send-loader")
    by_member = _loaded(member)
    assert by_member.sample is None
    with system_context(reason="tests.messaging_templates.send.load"):
        by_system = MessageTemplate.system_queryset().get(pk=by_member.pk)
    values = {"name": "Ana", "title": "Welcome"}
    assert _stored(_send(by_member, values)) == _stored(_send(by_system, values))


def test_a_named_kit_is_the_mails_brand(system_channel: Any) -> None:
    del system_channel
    with system_context(reason="tests.messaging_templates.send.kit"):
        other = BrandKit.objects.create(**{**ANGEE, "name": "Other", "is_default": False})
    assert _stored(_send(_greeting(), {"name": "Ana", "title": "Welcome"}, kit=other))[0] == "Welcome from Other"


def test_it_renders_in_the_rows_language_and_the_projects_time_zone(system_channel: Any) -> None:
    del system_channel
    _row("part", "close", language="es", source="Hasta pronto.")
    row = _mail(
        'Hola, {{ name }}.\n\nA las {{ when|date:"H:i" }}.\n\n{% include "parts/close" only %}',
        {"name": "El nombre.", "when": "Cuándo."}, language="es",
    )
    with override_settings(TIME_ZONE="Pacific/Auckland"):
        subject, text, html = _stored(_send(row, {"name": "Ana", "when": "2026-11-24T23:30:00+00:00"}))
    assert '<html lang="es" dir="ltr">' in html
    assert text == "Hola, Ana.\n\nA las 12:30.\n\nHasta pronto."


def test_a_send_costs_its_render_compose_as_system_and_one_row_fact_read(system_channel: Any) -> None:
    """The verb repeats nothing: a send to a known address executes the statements of its render, of
    ``compose_as_system`` for the same payload, and the one structural read of the row's side."""

    del system_channel
    row, values, address = _greeting(), {"name": "Ana", "title": "Welcome"}, "known@example.test"
    _send(row, values, to=address)
    with CaptureQueriesContext(connection) as render, system_context(reason=REASON):
        rendered = row.render(values)
    with CaptureQueriesContext(connection) as compose:
        Message.objects.compose_as_system(
            reason=REASON, to=[address], subject=rendered.subject, text=rendered.plain_text, html=rendered.html,
        )
    with CaptureQueriesContext(connection) as send:
        _send(row, values, to=address)
    assert len(send) == len(render) + len(compose) + 1, (len(send), len(render), len(compose))


# ---------- server code only ----------


def _references(source: str, name: str) -> bool:
    """Whether ``source`` refers to ``name`` in code, as messaging's guard of its writer reads one: an attribute, a
    name, the string handed to ``getattr``, an import alias; never prose, never the name's own ``def``."""

    return any(
        (isinstance(node, ast.Attribute) and node.attr == name)
        or (isinstance(node, ast.Name) and node.id == name)
        or (isinstance(node, ast.Constant) and node.value == name)
        or (isinstance(node, ast.alias) and name in (node.name, node.asname))
        for node in ast.walk(ast.parse(source))
    )


SYSTEM_SEND_CALLERS: frozenset[str] = frozenset()
"""The source modules that call ``send_as_system``, each added here in a reviewed change.

Call it only from system work whose own gate decided the send: a person's request reaches it only through such a
gate, never from a resolver, an MCP tool or a view that hands it a person's choice of row, address or values
unchecked. It checks no permission and decides nothing about the values, the links in them or who may be mailed."""

SHARED_STEP_CALLERS: frozenset[str] = frozenset({"src/angee/messaging_templates/models.py"})
"""The one module that may call the shared send step ``_render_and_record``, whose verbs ask the row's side first."""


def test_only_the_listed_modules_call_the_system_send_or_its_shared_step() -> None:
    """Every module under ``src`` and ``examples`` that refers to ``send_as_system`` or ``_render_and_record`` is
    listed. Messaging's guard (``tests/test_messaging_system_mail_callers.py``) names this addon once for its writer;
    this one names who reaches the template send, whose gate is each caller's own."""

    root = Path(__file__).resolve().parents[1]
    sources = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for base in ("src", "examples") for path in sorted((root / base).rglob("*.py"))
    }
    for name, allowed in (("send_as_system", SYSTEM_SEND_CALLERS), ("_render_and_record", SHARED_STEP_CALLERS)):
        found = {module for module, source in sources.items() if name in source and _references(source, name)}
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
    assert _references(source, "_render_and_record")
    assert not _references('def _render_and_record(self):\n    """See :meth:`_render_and_record`."""\n',
                           "_render_and_record")


# ---------- the test send ----------


SAMPLE = {"name": "Ana", "title": "Welcome"}


def _sampled(**values: Any) -> Any:
    """The greeting template whose sample renders."""

    return _greeting(sample=SAMPLE, **values)


def test_an_administrators_test_send_reaches_their_own_handle_marked_as_a_test(system_channel: Any, kit: Any) -> None:
    """Rendered from the sample as named system work, recorded on the system channel with the administrator as who
    caused it, read by administrators."""

    tester = create_platform_admin("messaging-templates-tester", email="Test.Admin@example.test")
    other = create_platform_admin("messaging-templates-other-admin")
    member = create_user("messaging-templates-test-onlooker")
    with system_context(reason="tests.messaging_templates.send.handle"):
        handle = Handle.objects.upsert(platform=Handle.Platform.EMAIL, value="test.admin@example.test")
    row = _sampled()
    rendered = _render(row, SAMPLE, kit)
    audits = _audits()
    message = MessageTemplate.objects.send_test(row, actor=tester)
    assert message.actor() is None
    assert _audits()[len(audits)] == (PermissionAuditEvent.KIND_SUDO_BYPASS, TEST_SEND_REASON)
    with system_context(reason="tests.messaging_templates.send.read"):
        stored: Any = Message._base_manager.select_related("thread").get(pk=message.pk)
        recipients = list(Recipient._base_manager.filter(message=stored).values_list("role", "handle_id"))
    assert (stored.channel_id, stored.status, stored.created_by_id, stored.thread.created_by_id) == (
        system_channel.pk, "queued", tester.pk, tester.pk,
    )
    assert recipients == [("to", handle.pk)]
    assert _stored(message) == ("[Test] Welcome from Acme", rendered.plain_text.strip(), rendered.html)
    assert stored.with_actor(other).has_access("read")
    assert not stored.with_actor(member).has_access("read")


@pytest.mark.parametrize("host", ["installed", "missing", "unconfigured"])
def test_a_non_administrator_learns_nothing_and_causes_no_render_read_or_audit_row(
    mailing: None, plain: Any, settings: Any, monkeypatch: pytest.MonkeyPatch, host: str,
) -> None:
    """A member and the system channel's own replier, on a host with the channel, without it, or without mail.

    ``preview`` is asked before the row's side, so a block or an overridden default is refused the same way.
    """

    del mailing, plain
    member = create_user("messaging-templates-test-member")
    replier = create_user("messaging-templates-test-replier")
    if host != "missing":
        grant(_install(), "replier", replier)
    if host == "unconfigured":
        settings.MAILERS = {}
    shipped = _greeting(key="tests.shipped", is_baseline=True, sample=SAMPLE)
    _greeting(key="tests.shipped", sample=SAMPLE)
    with system_context(reason="tests.messaging_templates.send.load"):
        rows = [_sampled(), _row("block", "card"), MessageTemplate._base_manager.get(pk=shipped.pk)]
    looked_up: list[Any] = []
    _recording(monkeypatch, ChannelManager, "system_channel", lambda manager: looked_up.append(manager))
    walked = _walks(monkeypatch)
    for actor in (member, replier):
        for row in rows:
            for key in (None, f"refused-{row.pk}"):
                audits = _audits()
                with _refused(PermissionDenied):
                    MessageTemplate.objects.send_test(row, actor=actor, idempotency_key=key)
                assert _audits() == audits, (row.kind, key)
                assert key is None or _claim(key, actor) is None, (row.kind, key)
    assert (walked, looked_up) == ([], [])


def test_an_account_with_no_address_writes_nothing_and_a_refused_render_leaves_the_system_works_audit_row(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The address is refused before the system's work starts; the render is that work, audited at once."""

    del system_channel
    addressless = create_platform_admin("messaging-templates-addressless", email="")
    tester = create_platform_admin("messaging-templates-sample-tester")
    row, unrenderable = _sampled(), _greeting(key="tests.unrenderable", sample={"title": "Welcome"})
    walked, audits = _walks(monkeypatch), _audits()
    with _refused(ValidationError) as refusal:
        MessageTemplate.objects.send_test(row, actor=addressless)
    assert (refusal.value.code, walked, _audits()) == ("no_test_address", [], audits)
    with _refused(ValidationError) as refusal:
        MessageTemplate.objects.send_test(unrenderable, actor=tester)
    assert [item.code for item in refusal.value.error_list] == ["invalid_values"]
    assert _audits() == [*audits, (PermissionAuditEvent.KIND_SUDO_BYPASS, TEST_SEND_REASON)]


def _union_terms(expression: Any) -> list[Any]:
    """The terms a permission expression unions: a fragment only ever widens a permission into such a union."""

    if isinstance(expression, PermBinOp) and expression.op == "+":
        return [*_union_terms(expression.left), *_union_terms(expression.right)]
    return [expression]


def test_every_signed_in_reader_reads_every_template_and_kit_in_the_composed_schema(db: None) -> None:
    """The condition the test send's elevated render rests on: it reads nothing its administrator's preview does not.

    In the schema the session synchronised, ``read`` on both definitions holds the owner's ``authenticated`` (widened
    only), the template's one ``read__`` gate is ``read__sample`` (which no render of an included row reads) and the
    kit has none.
    """

    del db
    definitions = {
        definition.resource_type: {permission.name: permission for permission in definition.permissions}
        for definition in backend().schema().definitions
        if definition.resource_type in ("messaging_templates/message_template", "brandkit/brand_kit")
    }
    expected = {"messaging_templates/message_template": ["read__sample"], "brandkit/brand_kit": []}
    for resource_type, gates in expected.items():
        permissions = definitions[resource_type]
        assert PermRef(name="authenticated") in _union_terms(permissions["read"].expression), resource_type
        assert sorted(name for name in permissions if name.startswith("read__")) == gates, resource_type


def test_a_signed_in_user_with_no_grant_reads_every_template_row_and_every_kit(kit: Any) -> None:
    """The same condition, at the rows: shipped defaults, overrides, own rows of every kind, a kit that is not the
    default."""

    del kit
    outsider = create_user("messaging-templates-send-outsider")
    _row("layout", "plain", is_baseline=True)
    _row("layout", "plain")
    _sampled()
    _row("block", "card")
    _row("part", "end", language="es")
    with system_context(reason="tests.messaging_templates.send.kit"):
        BrandKit.objects.create(**{**ANGEE, "name": "Other", "is_default": False})
    for model in (MessageTemplate, BrandKit):
        assert model.objects.with_actor(outsider).count() == model._base_manager.count() > 1, model


def _claim(key: str, actor: Any) -> Any:
    """The changeset that claims ``key`` for ``actor``'s test sends, or ``None``."""

    scope = f"messaging_templates.message_template.send_test:{to_subject_ref(actor)}"
    return changeset_model()._base_manager.filter(scope=scope, key=key).first()


def test_a_keyed_test_send_replays_its_one_message_and_another_request_under_the_key_conflicts(
    system_channel: Any, sent: list[str], django_capture_on_commit_callbacks: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry after a lost answer is one mail; the key names one request (the row and the kit) for one actor."""

    del system_channel
    tester = create_platform_admin("messaging-templates-retrier")
    colleague = create_platform_admin("messaging-templates-retrier-colleague")
    row, other = _sampled(), _sampled(key="tests.other")
    with system_context(reason="tests.messaging_templates.send.kit"):
        named = BrandKit.objects.create(**{**ANGEE, "name": "Other", "is_default": False})
    with django_capture_on_commit_callbacks(execute=True):
        first = MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="retry-1")
    claim = _claim("retry-1", tester)
    assert (claim.created_row, claim.actor_ref, sent) == (first, str(to_subject_ref(tester)), [DELIVER_MESSAGE_TASK])
    walked, messages = _walks(monkeypatch), Message._base_manager.count()
    with django_capture_on_commit_callbacks(execute=True):
        again = MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="retry-1")
    assert (again.pk, walked, Message._base_manager.count(), sent) == (first.pk, [], messages, [DELIVER_MESSAGE_TASK])
    with pytest.raises(CreationKeyConflict):
        MessageTemplate.objects.send_test(other, actor=tester, idempotency_key="retry-1")
    with pytest.raises(CreationKeyConflict):
        MessageTemplate.objects.send_test(row, actor=tester, kit=named, idempotency_key="retry-1")
    assert Message._base_manager.count() == messages
    theirs = MessageTemplate.objects.send_test(row, actor=colleague, idempotency_key="retry-1")
    assert theirs.pk != first.pk
    # The gate comes before the claim: a former administrator's retry of the very same press is refused.
    with system_context(reason="tests.messaging_templates.send.demote"):
        revoke_role(actor=tester, role="angee/role:admin")
    with _refused(PermissionDenied):
        MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="retry-1")


def test_a_key_names_one_press_so_an_edit_of_the_rows_text_or_sample_is_another_request(system_channel: Any) -> None:
    """A reused key after an edit conflicts instead of replaying the first, now stale, mail."""

    del system_channel
    tester = create_platform_admin("messaging-templates-edited-retrier")
    row = _sampled()
    first = MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="edited-1")
    assert MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="edited-1").pk == first.pk
    for column, value in (("source", "Hello again, {{ name }}."), ("sample", {**SAMPLE, "name": "Bea"})):
        edited = MessageTemplate._base_manager.get(pk=row.pk)
        setattr(edited, column, value)
        with system_context(reason="tests.messaging_templates.send.edit"):
            edited.save()
        with pytest.raises(CreationKeyConflict):
            MessageTemplate.objects.send_test(edited, actor=tester, idempotency_key="edited-1")


def test_a_keyed_retry_of_a_test_whose_message_was_trashed_is_refused_as_discarded(system_channel: Any) -> None:
    """Messaging's replay rule, not a success naming the trashed row."""

    del system_channel
    tester = create_platform_admin("messaging-templates-trashed-retrier")
    row = _sampled()
    first = MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="trashed-1")
    with system_context(reason="tests.messaging_templates.send.trash"):
        Message._base_manager.filter(pk=first.pk).update(is_trashed=True)
    messages = Message._base_manager.count()
    with pytest.raises(ValidationError) as refused:
        MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="trashed-1")
    assert refused.value.error_dict["idempotency_key"][0].code == "discarded"
    assert Message._base_manager.count() == messages


def test_a_test_send_to_an_address_the_transport_cannot_carry_is_refused_and_its_claim_rolled_back(
    system_channel: Any,
) -> None:
    """Refused by compose under ``to`` before any message row is written; a keyed refusal leaves its key free."""

    del system_channel
    tester = create_platform_admin("messaging-templates-uncarried", email="tëster@example.test")
    row = _sampled()
    with _refused(ValidationError, key="to", code="cannot_carry"):
        MessageTemplate.objects.send_test(row, actor=tester)
    audits = _audits()
    with pytest.raises(ValidationError) as refused:
        MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="uncarried-1")
    assert refused.value.error_dict["to"][0].code == "cannot_carry"
    # The claim's own transaction rolls back the claim and the render's audit row with it.
    assert (_claim("uncarried-1", tester), _audits()) == (None, audits)
    tester.email = "tester@example.test"
    with system_context(reason="tests.messaging_templates.send.address"):
        tester.save(update_fields=["email"])
    message = MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="uncarried-1")
    assert _claim("uncarried-1", tester).created_row == message
