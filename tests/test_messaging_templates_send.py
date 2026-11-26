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
from rebac import PermissionDenied, actor_context, anonymous_actor, current_actor, is_sudo, system_context
from rebac.models import PermissionAuditEvent

from angee.brandkit.testing.models import BrandKit
from angee.jobs.enqueue import celery_app
from angee.messaging.constants import DELIVER_MESSAGE_TASK
from angee.messaging.managers import ChannelManager, MessageManager
from angee.messaging.tasks import deliver_message
from angee.messaging.testing.models import Handle, Message, Part, Recipient
from angee.messaging_templates.testing.models import MessageTemplate
from angee.messaging_templates.walker import TemplateWalker
from tests.conftest import create_platform_admin, create_user
from tests.messaging_campaign import grant
from tests.test_messaging_compose import _refused
from tests.test_messaging_system_mail import LOCMEM, SENDER, _install
from tests.test_messaging_templates_render import ANGEE, _mail, _recording, _render, _row
from tests.test_messaging_templates_render import kit as kit
from tests.test_messaging_templates_render import plain as plain

ADDRESS = "reader@example.test"
REASON = "tests.messaging_templates.send"
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


def _names(source: str) -> set[str]:
    return {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute | ast.Name)
    }


SYSTEM_SEND_CALLERS: frozenset[str] = frozenset()
"""The source modules that may call ``send_as_system``, each named system work, each added here in a reviewed change.

It checks no permission of its own, so a module a person's request reaches with that person's row, address or
values (a resolver, an MCP tool, a view, a task fed by a request) is no caller of it."""


def test_only_the_listed_modules_call_the_system_send() -> None:
    """Every module under ``src`` and ``examples`` that names ``send_as_system`` in code, not prose, is listed."""

    root = Path(__file__).resolve().parents[1]
    callers = {
        str(path.relative_to(root))
        for base in ("src", "examples") for path in sorted((root / base).rglob("*.py"))
        if "send_as_system" in (source := path.read_text(encoding="utf-8")) and "send_as_system" in _names(source)
    }
    assert callers == SYSTEM_SEND_CALLERS


# ---------- the test send ----------


SAMPLE = {"name": "Ana", "title": "Welcome"}


def _sampled(**values: Any) -> Any:
    """The greeting template whose sample renders."""

    return _greeting(sample=SAMPLE, **values)


def test_an_administrators_test_send_reaches_their_own_handle_marked_as_a_test(system_channel: Any, kit: Any) -> None:
    """Rendered from the sample under the administrator, recorded on the system channel, read by administrators."""

    tester = create_platform_admin("messaging-templates-tester", email="Test.Admin@example.test")
    other = create_platform_admin("messaging-templates-other-admin")
    member = create_user("messaging-templates-test-onlooker")
    with system_context(reason="tests.messaging_templates.send.handle"):
        handle = Handle.objects.upsert(platform=Handle.Platform.EMAIL, value="test.admin@example.test")
    row = _sampled()
    rendered = _render(row, SAMPLE, kit)
    message = MessageTemplate.objects.send_test(row, actor=tester)
    assert message.actor() is None
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
            audits = _audits()
            with _refused(PermissionDenied):
                MessageTemplate.objects.send_test(row, actor=actor)
            assert _audits() == audits, row.kind
    assert (walked, looked_up) == ([], [])


def test_an_account_with_no_address_or_a_sample_that_does_not_render_is_refused_with_nothing_elevated(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The render runs as the administrator, so a refused test writes no permission audit row at all."""

    del system_channel
    addressless = create_platform_admin("messaging-templates-addressless", email="")
    tester = create_platform_admin("messaging-templates-sample-tester")
    row, unrenderable = _sampled(), _greeting(key="tests.unrenderable", sample={"title": "Welcome"})
    walked, audits = _walks(monkeypatch), _audits()
    with _refused(ValidationError) as refusal:
        MessageTemplate.objects.send_test(row, actor=addressless)
    assert (refusal.value.code, walked) == ("no_test_address", [])
    with _refused(ValidationError) as refusal:
        MessageTemplate.objects.send_test(unrenderable, actor=tester)
    assert [item.code for item in refusal.value.error_list] == ["invalid_values"]
    assert _audits() == audits
