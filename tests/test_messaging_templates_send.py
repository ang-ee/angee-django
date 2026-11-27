"""A message template sent on messaging's system channel: as named system work (``send_as_system``), and as an
administrator's test send (``send_test``)."""

from __future__ import annotations

import ast
import email.policy
import logging
import traceback
from collections.abc import Callable
from contextlib import nullcontext
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path
from typing import Any, cast

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.html import escape
from django.views.debug import ExceptionReporter
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
from angee.messaging.testing.models import Channel, Handle, Message, Part, Recipient
from angee.messaging_templates.engine import (
    INVALID_VALUES,
    NOT_A_TEMPLATE,
    NOT_EFFECTIVE,
    RENDER_FAILED,
    SEND_CODES,
    VALUE_NOT_LINKABLE,
    VALUE_NOT_PRINTED,
)
from angee.messaging_templates.testing.faults import failing_render
from angee.messaging_templates.testing.models import MessageTemplate
from angee.messaging_templates.walker import TemplateWalker, TemplateWarningCode
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
            NOT_A_TEMPLATE: [MessageTemplate.objects.get(kind="layout"), _row("block", "card"), _row("part", "end")],
            NOT_EFFECTIVE: [MessageTemplate._base_manager.get(pk=shipped.pk)],
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
    assert code == INVALID_VALUES and message.startswith("name ")
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


LINKED = {"link": "Where the reader signs in."}
LINK = {"link": "https://app.example.test/sign-in/once"}


def test_an_override_that_no_longer_prints_the_value_its_sender_needs_is_refused_with_nothing_sent(
    system_channel: Any, sent: list[str], django_capture_on_commit_callbacks: Any,
) -> None:
    """A deployment's override of a shipped sign-in mail that drops ``{{ link }}`` (and one that drops its
    declaration too) is refused, in both bodies; the shipped text sends, and so does the override with no
    requirement passed, as before."""

    del system_channel
    _mail("Hello. [Sign in]({{ link }})", LINKED, key="tests.signin", is_baseline=True)
    override = _mail("Hello, and welcome.", LINKED, key="tests.signin")
    undeclared = _mail("Hello, and welcome.", {}, key="tests.signin_bare")
    for row, expected in (
        (override, [f"The {body} body does not print link, which this mail is sent for."
                    for body in ("HTML", "plain-text")]),
        (undeclared, ["This message template does not declare link, so neither body prints it."]),
    ):
        with django_capture_on_commit_callbacks(execute=True) as callbacks, _refused(ValidationError) as refusal:
            _send(row, LINK, must_print=["link"])
        assert [(item.code, item.message) for item in refusal.value.error_list] == [
            (VALUE_NOT_PRINTED, message) for message in expected], row.key
        assert (callbacks, sent) == ([], [])
    assert "sign-in/once" not in _stored(_send(override, LINK))[1]
    shipped = _mail("Hello. [Sign in]({{ link }})", LINKED, key="tests.signin_shipped")
    subject, text, html = _stored(_send(shipped, LINK, must_print=["link"]))
    assert "https://app.example.test/sign-in/once" in text and "https://app.example.test/sign-in/once" in html


@pytest.mark.parametrize(
    ("make", "must_print", "unprinted"),
    [
        pytest.param(lambda: _mail("[Sign in]({{ link }})", LINKED), ["link"], [], id="both-bodies"),
        pytest.param(
            lambda: _row("template", "tests.html_only", format="html", variables=LINKED,
                         source='<p><a href="{{ link|url }}">Sign in</a></p>', text_source="Sign in."),
            ["link"], ["plain-text"], id="html-body-only",
        ),
        pytest.param(lambda: _mail("{% if link %}Sign in.{% endif %}", LINKED), ["link"], ["HTML", "plain-text"],
                     id="only-tested-is-not-printed"),
        pytest.param(lambda: _mail("{% if link %}[Sign in]({{ link }}){% endif %}", LINKED), ["link"], [],
                     id="printed-under-a-condition"),
        pytest.param(lambda: _mail("Hi {{ person.first_name }}.", {"person.first_name": "A name."}),
                     ["person.first_name"], [], id="a-nested-path"),
        pytest.param(lambda: (_row("part", "signin_line", source="[Sign in]({{ url }})", variables={"url": "Where."}),
                              _mail('{% include "parts/signin_line" with url=link only %}', LINKED))[1],
                     ["link"], [], id="printed-by-an-included-part"),
    ],
)
def test_a_required_value_must_be_printed_by_each_body_through_any_include(
    system_channel: Any, make: Callable[[], Any], must_print: list[str], unprinted: list[str],
) -> None:
    del system_channel
    row = make()
    values = {"link": "https://app.example.test/sign-in/once", "person": {"first_name": "Ana"}}
    if not unprinted:
        _send(row, {key: value for key, value in values.items() if key in {p.split(".")[0] for p in must_print}},
              must_print=must_print)
        return
    with _refused(ValidationError) as refusal:
        _send(row, {"link": values["link"]}, must_print=must_print)
    assert [(item.code, item.message) for item in refusal.value.error_list] == [
        (VALUE_NOT_PRINTED, f"The {body} body does not print {must_print[0]}, which this mail is sent for.")
        for body in unprinted]


SIGN_IN = "https://app.example.test/sign-in#token=a&b"
GATED = {**LINKED, "first_name": "The reader's first name."}
HTML_BODY = "The HTML body of this mail does not show link, which it is sent for."
TEXT_BODY = "The plain-text body of this mail does not show link, which it is sent for."
QUESTION_LIST = {"questions.*.title": "A question's title.", "questions.*.link": "Where it is answered."}


@pytest.mark.parametrize(
    ("make", "values", "must_print", "refusals"),
    [
        pytest.param(lambda: _mail("{% if first_name %}[Sign in]({{ link }}){% endif %}Hello.", GATED),
                     {"link": SIGN_IN, "first_name": ""}, ["link"], [HTML_BODY, TEXT_BODY], id="a-false-condition"),
        pytest.param(lambda: _mail("{% if first_name %}[Sign in]({{ link }}){% endif %}Hello.", GATED),
                     {"link": SIGN_IN, "first_name": "Ana"}, ["link"], [], id="a-true-condition"),
        pytest.param(lambda: _mail("[Sign in]({{ link }})", LINKED), {"link": ""}, ["link"],
                     ["link is empty, and this mail is sent for it."], id="a-blank-value"),
        pytest.param(lambda: _mail("Your code: {{ code }}", {"code": "A code."}), {"code": 123456}, ["code"],
                     ["code is not text, and this mail is sent for its text."], id="a-number"),
        pytest.param(lambda: _mail("Hi {{ name }}.", {"name": "A name."}), {"name": "Tom & 'Jerry' <x>"}, ["name"],
                     [], id="escaped-in-the-html"),
        pytest.param(lambda: _mail("Code {{ code|upper }}.", {"code": "A code."}), {"code": "abc"}, ["code"],
                     ["The HTML body of this mail does not show code, which it is sent for.",
                      "The plain-text body of this mail does not show code, which it is sent for."],
                     id="only-through-another-filter"),
        pytest.param(lambda: _row("template", "tests.subject_link", format="html", variables=LINKED, layout="plain",
                                  subject="Sign in: {{ link }}", source='<p><a href="{{ link|url }}">Sign in</a></p>',
                                  text_source="Sign in at the site."),
                     {"link": SIGN_IN}, ["link"],
                     ["The plain-text body does not print link, which this mail is sent for."],
                     id="the-subject-is-no-body"),
        pytest.param(lambda: _mail("{% for question in questions %}- [{{ question.title }}]({{ question.link }})\n"
                                   "{% endfor %}", QUESTION_LIST),
                     {"questions": []}, ["questions.*.link"], [], id="an-empty-list-passes"),
    ],
)
def test_must_print_holds_a_plain_path_to_what_this_mail_shows_and_a_list_path_to_its_text(
    system_channel: Any, sent: list[str], make: Callable[[], Any], values: dict[str, Any], must_print: list[str],
    refusals: list[str],
) -> None:
    del system_channel
    row = make()
    if not refusals:
        _send(row, values, must_print=must_print)
        return
    with _refused(ValidationError) as refusal:
        _send(row, values, must_print=must_print)
    assert [(item.code, item.message) for item in refusal.value.error_list] == [
        (VALUE_NOT_PRINTED, message) for message in refusals]
    assert sent == []


def test_a_generator_of_paths_is_read_once_for_both_checks(system_channel: Any, sent: list[str]) -> None:
    """The text check and the check of this mail both see the paths: a generator is not spent by the first."""

    del system_channel
    row = _mail("{% if first_name %}[Sign in]({{ link }}){% endif %}Hello.", GATED)
    with _refused(ValidationError) as refusal:
        _send(row, {"link": SIGN_IN, "first_name": ""}, must_print=(path for path in ["link"]))
    assert [item.message for item in refusal.value.error_list] == [HTML_BODY, TEXT_BODY]
    assert sent == []


@pytest.mark.parametrize("entry", ["walk", "render", "send"])
def test_must_print_is_an_iterable_of_paths_never_one_path(system_channel: Any, entry: str) -> None:
    """Refused wherever a sender renders: the walk a batch caller renders itself, the row's render, the send."""

    del system_channel
    row = _mail("[Sign in]({{ link }})", LINKED)
    with pytest.raises(TypeError, match="Pass must_print as an iterable of paths, not one path."):
        if entry == "send":
            _send(row, {"link": SIGN_IN}, must_print="link")
        with system_context(reason="tests.messaging_templates.send.bare"):
            if entry == "render":
                row.render({"link": SIGN_IN}, must_print="link")
            walk = TemplateWalker().walk(row, MessageTemplate.objects)
            walk.render({"link": SIGN_IN}, kit=row._brand_kit(None), must_print="link")


DECISION = "http://localhost:8000/decisions/dec_1"


def _decision(key: str = "tests.decision", **values: Any) -> Any:
    """A mail that exists for one link: a button in the HTML body, the link after its label in the plain text."""

    return _mail("[Open the decision]({{ link }})", LINKED, key=key, **values)


@pytest.mark.parametrize("must_print", [[], ["link"]], ids=["no-requirement", "must-print"])
def test_a_link_the_mail_cannot_carry_is_refused_before_anything_is_written_not_sent_empty(
    system_channel: Any, sent: list[str], settings: Any, django_capture_on_commit_callbacks: Any, must_print: list[str],
) -> None:
    """A single-label host that is not this deployment's origin: the rule cannot link to it, so the send is refused
    in band, naming the value's path and the host, instead of sending an empty ``href`` and a label with nothing after
    it."""

    del system_channel
    settings.ANGEE_PUBLIC_ORIGIN = "https://app.example.test"
    row = _decision()
    with django_capture_on_commit_callbacks(execute=True) as callbacks, _refused(ValidationError) as refusal:
        _send(row, {"link": DECISION}, must_print=must_print)
    assert [(item.code, item.message) for item in refusal.value.error_list] == [
        (VALUE_NOT_LINKABLE, "link is not a link this mail can carry: the mail cannot link to localhost:8000."),
    ]
    assert (callbacks, sent) == ([], [])


def test_an_optional_link_left_blank_is_sent_as_it_was(system_channel: Any, settings: Any) -> None:
    del system_channel
    settings.ANGEE_PUBLIC_ORIGIN = "https://app.example.test"
    for blank in ("", "   "):
        assert 'href=""' in _stored(_send(_decision(key=f"tests.decision_{len(blank)}"), {"link": blank}))[2]


def test_a_keyed_test_send_whose_sample_the_mail_cannot_link_is_refused_with_no_claim(
    system_channel: Any, settings: Any, sent: list[str],
) -> None:
    del system_channel
    settings.ANGEE_PUBLIC_ORIGIN = "https://app.example.test"
    tester = create_platform_admin("messaging-templates-unlinkable-tester")
    row = _decision(sample={"link": DECISION})
    with pytest.raises(ValidationError) as refusal:
        MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="unlinkable-1")
    assert [item.code for item in refusal.value.error_list] == [VALUE_NOT_LINKABLE]
    assert (_claim("unlinkable-1", tester), Message._base_manager.count(), sent) == (None, 0, [])


def test_the_preview_shows_a_sample_link_the_mail_cannot_carry_as_its_render_error(plain: Any, settings: Any) -> None:
    """As for any sample value the render refuses: no rendering, and one warning with the refusal's words."""

    del plain
    settings.ANGEE_PUBLIC_ORIGIN = "https://app.example.test"
    row = _decision(sample={"link": DECISION})
    with system_context(reason="tests.messaging_templates.send.preview"):
        preview = row.preview()
    assert preview.rendered is None
    assert [(warning.code, warning.detail) for warning in preview.warnings] == [(
        TemplateWarningCode.RENDER_ERROR,
        "link is not a link this mail can carry: the mail cannot link to localhost:8000.",
    )]


LINKS = [
    pytest.param("", id="blank"),
    pytest.param("  ", id="white-space"),
    pytest.param("https://app.example.test/decisions/1?tab=a&b=c#top", id="two-labels"),
    pytest.param("https://app.example.test:8443/decisions/1", id="two-labels-port"),
    pytest.param("mailto:someone@example.test", id="mailto"),
    pytest.param(DECISION, id="foreign-single-label"),
    pytest.param("http://10.0.0.1/decisions/1", id="an-address"),
    pytest.param("/decisions/1", id="path-relative"),
    pytest.param("javascript:alert(1)", id="javascript"),
    pytest.param("https://user:secret@app.example.test/", id="user-info"),
    pytest.param("https:///decisions/1", id="no-host"),
    pytest.param("https://app.example.test/" + "a" * 2_048, id="too-long"),
]


@pytest.mark.parametrize("value", LINKS)
def test_the_public_link_check_answers_as_the_render_does(plain: Any, settings: Any, value: str) -> None:
    """The check a caller asks before a run gives the link the render prints, or exactly the render's refusal."""

    del plain
    settings.ANGEE_PUBLIC_ORIGIN = "https://app.example.test"
    row = _decision(key="tests.decision_check")
    try:
        link, expected = MessageTemplate.objects.clean_link(value), None
    except ValidationError as refusal:
        link, expected = None, [(item.code, item.message) for item in refusal.error_list]
    with system_context(reason="tests.messaging_templates.send.check"):
        if expected is not None:
            with pytest.raises(ValidationError) as rendered_refusal:
                row.render({"link": value})
            assert [(item.code, item.message) for item in rendered_refusal.value.error_list] == expected
            assert [code for code, _ in expected] == [VALUE_NOT_LINKABLE]
            return
        rendered = row.render({"link": value})
    assert link is not None
    assert f'href="{escape(link)}"' in rendered.html and f"Open the decision ({link})" in rendered.plain_text


@pytest.mark.parametrize(
    ("origin", "debug", "value", "linked"),
    [
        pytest.param("https://localhost:8000", False, "https://localhost:8000/decisions/1", True, id="own-https-port"),
        pytest.param("http://localhost:8000", True, DECISION, True, id="own-http-in-debug"),
        pytest.param("http://LocalHost:8000/", True, DECISION, True, id="own-spelled-otherwise"),
        pytest.param("http://127.0.0.1:8000", True, "http://127.0.0.1:8000/decisions/1", True, id="own-address"),
        pytest.param("http://[::1]:8000", True, "http://[::1]:8000/decisions/1", True, id="own-ipv6-address"),
        pytest.param("https://app.example.test", False, "https://app.example.test/d", True, id="two-labels"),
        pytest.param("http://localhost:8000", False, DECISION, False, id="http-origin-outside-debug"),
        pytest.param("http://localhost:8000", True, "http://localhost:8001/decisions/1", False, id="another-port"),
        pytest.param("http://localhost:8000", True, "https://localhost:8000/decisions/1", False, id="another-scheme"),
        pytest.param("http://localhost:8000", True, "http://intranet/decisions/1", False, id="a-foreign-host"),
        pytest.param("http://127.0.0.1:8000", True, "http://10.0.0.1/decisions/1", False, id="a-foreign-address"),
        pytest.param("", False, DECISION, False, id="no-origin"),
    ],
)
def test_the_deployments_own_public_origin_is_linked_in_both_bodies_and_no_other_single_label_host(
    system_channel: Any, settings: Any, origin: str, debug: bool, value: str, linked: bool,
) -> None:
    """The link rule asks base for the deployment's origin: whatever host that is, with its port, under the owner's
    own DEBUG rule; the public check answers alike in every case."""

    del system_channel
    settings.ANGEE_PUBLIC_ORIGIN, settings.DEBUG = origin, debug
    row = _decision(key="tests.decision_origin")
    if not linked:
        with pytest.raises(ValidationError) as checked:
            MessageTemplate.objects.clean_link(value)
        with _refused(ValidationError) as refusal:
            _send(row, {"link": value}, must_print=["link"])
        assert refusal.value.error_list[0].message == checked.value.error_list[0].message
        assert [item.code for item in refusal.value.error_list] == [VALUE_NOT_LINKABLE]
        return
    link = MessageTemplate.objects.clean_link(value)
    subject, text, html = _stored(_send(row, {"link": value}, must_print=["link"]))
    assert link.lower() == value.lower()
    assert f'href="{escape(link)}"' in html and f"Open the decision ({link})" in text


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("http://intranet:8080%23token=SECRET-marker", id="an-encoded-hash"),
        pytest.param("http://intranet:8080%2Ftoken=SECRET-marker", id="an-encoded-slash"),
        pytest.param("http://intranet:8080%3Ftoken=SECRET-marker", id="an-encoded-question-mark"),
        pytest.param("http://intranet;token=SECRET-marker", id="a-semicolon"),
        pytest.param("http://intranet token=SECRET-marker", id="a-space"),
    ],
)
def test_a_refused_link_names_no_part_of_it_past_its_host(plain: Any, settings: Any, value: str) -> None:
    """A refusal's words reach an action's answer, the preview and logs: one naming a host never runs into the rest
    of the link, whatever spelling hides its separator."""

    del plain
    settings.ANGEE_PUBLIC_ORIGIN = "https://app.example.test"
    with pytest.raises(ValidationError) as checked:
        MessageTemplate.objects.clean_link(value)
    with system_context(reason="tests.messaging_templates.send.echo"), pytest.raises(ValidationError) as rendered:
        _decision(key="tests.decision_echo").render({"link": value})
    for refusal in (checked.value, rendered.value):
        assert [item.code for item in refusal.error_list] == [VALUE_NOT_LINKABLE]
        assert all("SECRET-marker" not in item.message for item in refusal.error_list), refusal.messages


CALLERS_OF_THE_RENDER = ["saved preview", "preview of unsaved edits", "test send", "send_as_system"]


def _rendered_or_refused(caller: str, value: str, tester: Any) -> tuple[str, str]:
    """What one caller of the render answers for a sample whose link is ``value``: the link the plain text shows, or
    the refusal's words (a send's refusal, a preview's one warning)."""

    sampled = {"link": value}
    if caller == "preview of unsaved edits":
        row = _decision(key="tests.decision_patched", sample={"link": "https://app.example.test/saved"})
        with actor_context(tester):
            preview = row.preview(patch={"sample": sampled})
    elif caller == "saved preview":
        row = _decision(key="tests.decision_saved", sample=sampled)
        with actor_context(tester):
            preview = row.preview()
    else:
        row = _decision(key=f"tests.decision_{caller.replace(' ', '_')}", sample=sampled)
        try:
            message = (MessageTemplate.objects.send_test(row, actor=tester) if caller == "test send"
                       else _send(row, sampled))
        except ValidationError as refusal:
            return "refused", " ".join(refusal.messages)
        return "rendered", _stored(message)[1]
    if preview.rendered is None:
        assert [warning.code for warning in preview.warnings] == [TemplateWarningCode.RENDER_ERROR] * len(
            preview.warnings)
        return "refused", " ".join(warning.detail for warning in preview.warnings)
    return "rendered", preview.rendered.plain_text


@pytest.mark.parametrize(
    ("value", "outcome", "words"),
    [
        pytest.param("http://intranet:8080/decisions/1", "refused",
                     "link is not a link this mail can carry: the mail cannot link to intranet:8080.",
                     id="a-host-the-rule-refuses"),
        pytest.param("https://localhost:8000/decisions/1", "rendered", "https://localhost:8000/decisions/1",
                     id="the-deployments-own-origin"),
        pytest.param("", "rendered", "Open the decision ()", id="a-blank-optional-link"),
    ],
)
def test_every_caller_of_the_render_answers_one_sample_alike(
    system_channel: Any, settings: Any, value: str, outcome: str, words: str,
) -> None:
    """The saved preview, the preview of unsaved edits, the test send and ``send_as_system`` run one render: a link
    the rule would empty is the preview's one ``render_error`` warning and a send's refusal, in the same words; the
    deployment's own origin and a blank optional link render alike."""

    del system_channel
    settings.ANGEE_PUBLIC_ORIGIN = "https://localhost:8000"
    tester = create_platform_admin("messaging-templates-render-callers")
    answers = {caller: _rendered_or_refused(caller, value, tester) for caller in CALLERS_OF_THE_RENDER}
    assert {caller: answer[0] for caller, answer in answers.items()} == dict.fromkeys(CALLERS_OF_THE_RENDER, outcome)
    assert all(words in answer[1] for answer in answers.values()), answers
    if outcome == "refused":
        assert len({answer[1] for answer in answers.values()}) == 1, answers


SECRET = "https://app.example.test/sign-in/SECRET-marker"


def _leaks(error: BaseException) -> list[tuple[str, str, str]]:
    """Each variable a Django exception report would show holding the marker in any case (an address is stored in
    lower case), outside the test's own frames: the send's, the render's and messaging's system write alike."""

    reporter = ExceptionReporter(None, type(error), error, error.__traceback__)
    return [
        (frame["filename"], frame["function"], name)
        for frame in reporter.get_traceback_frames()
        if "/tests/" not in frame["filename"]
        for name, value in frame["vars"] if "secret-marker" in repr(value).lower()
    ]


@pytest.mark.parametrize("entry", ["send_as_system", "render"])
def test_an_exception_inside_the_render_reports_no_value_of_the_mail(system_channel: Any, entry: str) -> None:
    """A value the render refuses half-way (no JSON holds an ``object()``), raised with the credential beside it."""

    del system_channel
    row = _mail("[Sign in]({{ link }})", LINKED)
    values = {"link": SECRET, "unwritable": object()}
    with pytest.raises(ValidationError) as raised:
        if entry == "render":
            with system_context(reason="tests.messaging_templates.send.render"):
                row.render(values)
        else:
            _send(row, values)
    assert _leaks(raised.value) == []


def test_an_exception_inside_compose_reports_no_value_or_rendered_text_in_the_sends_frames(
    mailing: None, plain: Any, settings: Any,
) -> None:
    """Rendered, then refused by compose (system mail not configured): the send's own frames hold the values and the
    rendered text, the credential in both."""

    del mailing, plain
    _install()
    settings.MAILERS = {}
    with pytest.raises(ValidationError) as raised:
        _send(_mail("[Sign in]({{ link }})", LINKED), {"link": SECRET})
    assert _leaks(raised.value) == []


def test_a_report_of_the_link_checks_refusal_shows_no_part_of_the_link(settings: Any) -> None:
    """A caller that lets the check's refusal propagate: neither the check's frame nor the rule's below it shows the
    link, and the refusal's own words name only its host."""

    settings.ANGEE_PUBLIC_ORIGIN = "https://app.example.test"
    with pytest.raises(ValidationError) as raised:
        MessageTemplate.objects.clean_link("http://intranet/decisions?token=SECRET-marker")
    assert _leaks(raised.value) == []
    assert "SECRET-marker" not in str(raised.value)


@pytest.mark.parametrize("entry", ["send_as_system", "send_test"])
def test_an_exception_inside_compose_reports_no_recipient_address(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch, entry: str,
) -> None:
    """The address a send goes to is a local of the send's frames: a report shows it in none of them. The write
    itself refuses (its sender lookup), so every frame of the send, the shared step's among them, is on the
    traceback."""

    del system_channel

    def refuse(self: Any) -> Any:
        raise ValidationError("The sender lookup refused.")

    monkeypatch.setattr(Channel, "outbound_sender", refuse)
    address = "SECRET-marker@example.test"
    with pytest.raises(ValidationError) as raised:
        if entry == "send_as_system":
            _send(_mail("Hello.", {}, key="tests.addressed"), {}, to=address)
        tester = create_platform_admin("messaging-templates-addressed-tester")
        tester.email = address
        with system_context(reason="tests.messaging_templates.send.address"):
            tester.save(update_fields=["email"])
        MessageTemplate.objects.send_test(_sampled(), actor=tester)
    assert "_render_and_record" in [frame.name for frame in traceback.extract_tb(raised.value.__traceback__)]
    assert _leaks(raised.value) == []


@pytest.mark.parametrize("failure", ["an unknown column", "the save check raising"])
def test_a_failure_out_of_a_preview_of_unsaved_edits_reports_no_sample_or_patch(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """A failure that propagates out of the preview, a caller's column outside the edited ones or an unexpected
    exception from the save's check, with the marker in the saved sample and in the patch: no frame shows it."""

    del system_channel
    tester = create_platform_admin("messaging-templates-preview-reporter")
    row = _decision(key="tests.decision_reported", sample={"link": "https://app.example.test/x?SECRET-marker=saved"})
    patch: dict[str, Any] = {"sample": {"link": "https://app.example.test/x?token=SECRET-marker"}}
    if failure == "an unknown column":
        patch["key"] = "elsewhere"
    else:
        def raising(self: Any, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("The save check failed.")

        monkeypatch.setattr(MessageTemplate, "full_clean", raising)
    with actor_context(tester), pytest.raises((ValueError, RuntimeError)) as raised:
        row.preview(patch=patch)
    assert "preview" in [frame.name for frame in traceback.extract_tb(raised.value.__traceback__)]
    assert _leaks(raised.value) == []


def test_a_failure_after_a_keyed_test_send_wrote_its_message_reports_no_part_of_the_mail(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keyed path holds the written message, whose string form is its preview: a failure after the write, with
    the marker in the sample and so in the body, shows it in no frame from ``send_test`` down."""

    del system_channel
    tester = create_platform_admin("messaging-templates-keyed-reporter")
    row = _greeting(key="tests.keyed_reported", sample={**SAMPLE, "name": "SECRET-marker"})

    def refusing(self: Any, created: Any) -> None:
        raise RuntimeError("The changeset refused the record.")

    monkeypatch.setattr(changeset_model(), "record_created", refusing)
    with pytest.raises(RuntimeError) as raised:
        MessageTemplate.objects.send_test(row, actor=tester, idempotency_key="reported-1")
    frames = [frame.name for frame in traceback.extract_tb(raised.value.__traceback__)]
    assert "send_test" in frames and frames[-1] == "refusing"
    assert _leaks(raised.value) == []


@pytest.mark.parametrize("caller", ["send_as_system", "test send", "preview"])
def test_an_engine_failure_quoting_a_value_logs_the_templates_identity_and_type_and_never_the_value(
    system_channel: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, caller: str,
) -> None:
    """An engine exception whose text quotes a value the render was given (a sign-in link): the one error record
    names the template and the exception's type; no record holds the value, in its message, its arguments, its
    exception or its traceback; the refusal the caller gets is as before."""

    del system_channel
    link = "https://app.example.test/sign-in#token=SECRET-marker"
    row = _decision(key="tests.decision_failing", sample={"link": link})
    tester = create_platform_admin("messaging-templates-failing-render")

    with failing_render(RuntimeError(f"engine failed near {link}")), caplog.at_level(logging.DEBUG):
        if caller == "preview":
            with actor_context(tester):
                preview = row.preview()
            refusals = [(warning.code, warning.detail) for warning in preview.warnings]
            assert refusals == [(TemplateWarningCode.RENDER_ERROR,
                                 f"The message template did not render: RuntimeError: engine failed near {link}")]
        else:
            with pytest.raises(ValidationError) as refusal:
                if caller == "test send":
                    MessageTemplate.objects.send_test(row, actor=tester)
                else:
                    _send(row, {"link": link})
            assert [(item.code, item.message % (item.params or {})) for item in refusal.value.error_list] == [
                (RENDER_FAILED, f"The message template did not render: RuntimeError: engine failed near {link}")]
    for record in caplog.records:
        held = (record.getMessage(), repr(record.args), repr(record.exc_info), str(record.exc_text))
        assert all("SECRET-marker" not in text for text in held), record
    named = [record for record in caplog.records if record.name == "angee.messaging_templates.walker"]
    assert [(record.levelno, record.getMessage()) for record in named] == [(
        logging.ERROR,
        "A message template failed to render: the template tests.decision_failing in en raised RuntimeError.",
    )]


def _raised_codes(tree: ast.AST) -> list[ast.expr]:
    """Each code a raise site in ``tree`` names: a ``code=`` keyword, or the code handed to ``_refused``. The body of
    ``_refused`` itself forwards the code its callers hand it, so it is read at those calls instead."""

    found: list[ast.expr] = []
    forwarding = {id(node) for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef) and fn.name == "_refused"
                  for node in ast.walk(fn)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and id(node) not in forwarding:
            found.extend(keyword.value for keyword in node.keywords if keyword.arg == "code")
            if isinstance(node.func, ast.Name) and node.func.id == "_refused" and len(node.args) == 2:
                found.append(node.args[1])
    return found


def test_every_code_a_send_raises_is_an_engine_constant_in_the_exported_set() -> None:
    """The render's raise sites (``engine.py``, ``walker.py``) and a send's row and kit refusals (``send_refusal``,
    ``_brand_kit``) each name their code by an engine constant, never a retyped string, and ``SEND_CODES`` is exactly
    what they raise: a code added without the set, or left in it unraised, fails here."""

    from angee.messaging_templates import engine as engine_module

    addon = Path(engine_module.__file__).parent
    trees: list[ast.AST] = [ast.parse((addon / name).read_text()) for name in ("engine.py", "walker.py")]
    models = ast.parse((addon / "models.py").read_text())
    trees += [node for node in ast.walk(models)
              if isinstance(node, ast.FunctionDef) and node.name in {"send_refusal", "_brand_kit"}]
    codes = [code for tree in trees for code in _raised_codes(tree)]
    assert codes and all(isinstance(code, ast.Name) for code in codes), [ast.unparse(code) for code in codes]
    raised = {getattr(engine_module, cast(ast.Name, code).id) for code in codes}
    assert raised == SEND_CODES, (sorted(raised - SEND_CODES), sorted(SEND_CODES - raised))


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
    assert [item.code for item in refusal.value.error_list] == [INVALID_VALUES]
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
    for column, value in (("source", "Hello again, {{ name }}."), ("sample", {**SAMPLE, "name": "Bea"})):
        row, key = _sampled(key=f"tests.edited_{column}"), f"edited-{column}"
        first = MessageTemplate.objects.send_test(row, actor=tester, idempotency_key=key)
        assert MessageTemplate.objects.send_test(row, actor=tester, idempotency_key=key).pk == first.pk
        edited = MessageTemplate._base_manager.get(pk=row.pk)
        setattr(edited, column, value)
        with system_context(reason="tests.messaging_templates.send.edit"):
            edited.save()
        with pytest.raises(CreationKeyConflict):
            MessageTemplate.objects.send_test(edited, actor=tester, idempotency_key=key)


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
