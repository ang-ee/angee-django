"""Vendor-neutral outbound email delivery through django-anymail."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from contextvars import ContextVar
from email.utils import formataddr, getaddresses
from smtplib import SMTPRecipientsRefused, SMTPResponseException, SMTPServerDisconnected
from typing import Any

from anymail.exceptions import (
    AnymailAPIError,
    AnymailInvalidAddress,
    AnymailRecipientsRefused,
    AnymailRequestsAPIError,
    AnymailUnsupportedFeature,
)
from anymail.message import AnymailMessage
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, EmailMultiAlternatives, mailers
from django.core.mail.backends.base import BaseEmailBackend
from django.db import DatabaseError, transaction
from django.utils.encoding import force_str, punycode
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ConnectTimeout
from urllib3.exceptions import MaxRetryError, NewConnectionError

from angee.base.serialization import strip_null_bytes
from angee.integrate.errors import IntegrationError, is_transient_status
from angee.messaging.backends import ChannelBackend, DeliveryOutcome, DeliveryUncertain, OutboundEmailNotConfigured
from angee.messaging.constants import SYSTEM_CHANNEL_BACKEND, TRANSPORT_MAILER_ALIAS

logger = logging.getLogger(__name__)

# Rendered from the message's own facts (sender, recipients, TITLE, external id, parent),
# so a retained header part never adds a second copy of one.
_ENVELOPE_HEADERS = frozenset({"bcc", "cc", "from", "in-reply-to", "message-id", "references", "subject", "to"})
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_ENCODED_WORD_OPENER = re.compile(r"=(?=\?[^?]*\?[bq]\?)", re.IGNORECASE)
"""The ``=`` that opens an RFC 2047 encoded word: a mail library decodes one with any or no charset, either
encoding letter in either case, terminated or not, and inside a word too."""


def header_text(text: str) -> str:
    """Return ``text`` as an outgoing mail header carries it: one line, with no control character and no encoded word.

    Each control character and line break becomes a space, and every encoded-word
    opener (``=?charset?q?`` or ``?b?``) is broken with a space after its ``=``, so
    neither a mail library nor a reader's client decodes anything out of it, and no
    provider that receives the text as a string can either: the text written, the
    text on the wire and the text a reader sees are the same string. Text that
    opens no word (``2+2=?``) is kept. Every outgoing subject takes it (a typed one
    and one derived from a conversation's title), and so does every display name
    this backend writes; a typed recipient it would change is refused
    (``MessageManager.clean_recipient``).
    """

    text = "".join(
        " " if unicodedata.category(character) in ("Cc", "Zl", "Zp") else character
        for character in strip_null_bytes(text or "")
    )
    return _ENCODED_WORD_OPENER.sub("= ", text).strip()


_DELIVERING: ContextVar[bool] = ContextVar("messaging_email_delivering", default=False)
"""Whether this context is delivering a recorded message through the transport mailer.

Set by :meth:`AnymailEmailChannelBackend.deliver` around the transport's
connection; the system mailer refuses to record while it is set, so no mailer
can hand a delivery back to it. A context variable: it closes re-entry in the
delivering thread, not a hand-off to another thread or a queue.
"""


def _valid_sender(value: str) -> tuple[str, str]:
    """Return ``value`` as the ``(name, address)`` email sender it names, or refuse it under ``channel``.

    The one sender rule: ``value`` is one address as written, which the handle
    owner lands (``MessageManager.clean_recipient``, the check every address
    ``compose`` lands takes), and which the transport can carry
    (:func:`require_carried`). A sender accepted here is never refused, nor read
    as another address, once a message is being written or delivered. It reads nothing.
    """

    try:
        name, address = apps.get_model("messaging", "Message").objects.clean_recipient(str(value or ""), key="channel")
    except ValidationError as error:
        raise ValidationError({"channel": ValidationError(
            "Configure a valid sender address for this email channel (its from_email, else DEFAULT_FROM_EMAIL).",
            code="invalid",
        )}) from error
    require_carried(address, key="channel")
    return name, address


class AnymailEmailChannelBackend(ChannelBackend):
    """Send an email channel's outbound messages through Django's transport mailer.

    The deployment names the mailer that sends as ``MAILERS["transport"]`` (Django's
    SMTP backend or an Anymail ESP backend, with its ``ANYMAIL``/``ANYMAIL_*``
    settings); Django's default mailer is messaging's system mailer, which records
    mail and sends none. This adapter remains provider-neutral and also works with
    Anymail's deterministic test backend.
    """

    key = "email"
    label = "Email"
    icon = "mail"

    @classmethod
    def outbound_email_configured(cls) -> bool:
        """Whether the deployment names a transport mailer, ``MAILERS["transport"]``; read from settings alone."""

        return TRANSPORT_MAILER_ALIAS in mailers

    @classmethod
    def default_sender(cls) -> tuple[str, str]:
        """Return the deployment's default sender, ``DEFAULT_FROM_EMAIL``, from settings alone.

        The sender of a channel that names none of its own. A deployment with no
        transport mailer raises :class:`OutboundEmailNotConfigured`; an address the
        sender rule refuses is refused under ``channel`` (:func:`_valid_sender`).
        """

        if not cls.outbound_email_configured():
            raise OutboundEmailNotConfigured
        return _valid_sender(str(settings.DEFAULT_FROM_EMAIL))

    def outbound_sender(self) -> tuple[str, str]:
        """Resolve and validate this channel's configured default envelope sender.

        The channel's ``from_email`` config wins; a channel that names none sends from
        the deployment's sender (:meth:`default_sender`). An unconfigured transport
        raises :class:`OutboundEmailNotConfigured`; an invalid address is refused
        under ``channel`` (:func:`_valid_sender`).
        """

        if not self.outbound_email_configured():
            raise OutboundEmailNotConfigured
        config = self.bridge.config if isinstance(self.bridge.config, dict) else {}
        from_email = config.get("from_email")
        return _valid_sender(str(from_email)) if from_email else self.default_sender()

    def deliver(self, message: Any) -> DeliveryOutcome:
        """Render and send ``message`` when an email transport is configured."""

        if not self.outbound_email_configured():
            logger.warning(
                "Outbound email delivery is not configured; message %s was not sent.",
                message.pk,
            )
            raise IntegrationError("Outbound email delivery is not configured.")
        try:
            email = self.email_message(message)
        except IntegrationError:
            # Rendering has not entered the transport; its own reason is the one to keep.
            raise
        except Exception as error:
            # Rendering has not entered the transport, so it cannot have sent mail.
            raise IntegrationError("The email could not be rendered.") from error
        if not email.from_email or not (email.to or email.cc or email.bcc):
            logger.warning(
                "Outbound email message %s has no complete from/to envelope; it was not sent.",
                message.pk,
            )
            raise IntegrationError("The email envelope is incomplete.")
        connection = mailers[TRANSPORT_MAILER_ALIAS]
        delivering = _DELIVERING.set(True)
        try:
            try:
                connection.open()
            except SMTPResponseException as error:
                raise IntegrationError(
                    "The mail server refused the connection.", transient=400 <= error.smtp_code < 500,
                ) from error
            except (OSError, SMTPServerDisconnected) as error:
                # RequestNotSentError names HTTP; email uses the same transient
                # non-acceptance contract with an email-specific safe message.
                raise IntegrationError("The email connection failed before sending.", transient=True) from error
            return self._send(email, connection)
        finally:
            try:
                connection.close()
            except Exception as error:
                logger.warning("Closing the email connection failed (%s).", type(error).__name__)
            _DELIVERING.reset(delivering)

    @staticmethod
    def _send(email: Any, connection: BaseEmailBackend) -> DeliveryOutcome:
        """Once sending starts, only explicit provider replies prove refusal."""

        try:
            accepted = connection.send_messages([email])
        except (AnymailInvalidAddress, AnymailRecipientsRefused, AnymailUnsupportedFeature) as error:
            raise IntegrationError("The email provider refused the message.") from error
        except SMTPRecipientsRefused as error:
            codes = [code for code, _text in error.recipients.values()]
            raise IntegrationError(
                "The email recipients were refused.",
                transient=bool(codes) and all(400 <= code < 500 for code in codes),
            ) from error
        except SMTPResponseException as error:
            # Explicit SMTP replies are definite refusals, including DATA rejection.
            raise IntegrationError(
                "The mail server refused delivery.", transient=400 <= error.smtp_code < 500,
            ) from error
        except AnymailAPIError as error:
            # AnymailRequestsBackend chains the real requests exception. A
            # generic ConnectionError may follow a sent request (ProtocolError),
            # but ConnectTimeout and urllib3's NewConnectionError (including DNS
            # NameResolutionError) prove that the connection was never opened.
            cause = error.__cause__
            if isinstance(error, AnymailRequestsAPIError) and error.status_code is None and (
                isinstance(cause, ConnectTimeout)
                or (isinstance(cause, RequestsConnectionError) and cause.args
                    and isinstance(cause.args[0], MaxRetryError)
                    and isinstance(cause.args[0].reason, NewConnectionError))
            ):
                raise IntegrationError("The email connection failed before sending.", transient=True) from error
            if error.status_code is not None and 400 <= error.status_code < 500:
                raise IntegrationError(
                    "The email provider refused delivery.", transient=is_transient_status(error.status_code),
                ) from error
            # HTTP 5xx, timeouts and lost acknowledgements may follow acceptance.
            raise DeliveryUncertain("The email provider did not confirm delivery.") from error
        except OSError as error:
            raise DeliveryUncertain("The mail server did not confirm delivery.") from error
        if not accepted:
            logger.warning("The configured email backend declined the message.")
        return DeliveryOutcome(accepted=bool(accepted))

    def email_message(self, message: Any) -> AnymailMessage:
        """Render a messaging ``Message`` into Anymail's provider-neutral message."""

        part_model = apps.get_model("messaging", "Part")
        parts = part_model.objects.reading_order_for_message(message)
        sender = self._sender(message)
        recipients = self._recipients(message)
        subject = next(
            (
                fragment.text
                for part in parts
                if part.role == part_model.PartRole.TITLE and (fragment := part.fragment) is not None
            ),
            "",
        )
        plain, html = self._body(parts, part_model)
        extra_headers = self._headers(parts, part_model)
        if message.external_id:
            extra_headers["Message-ID"] = _message_id(str(message.external_id))
            extra_headers["X-Angee-External-ID"] = str(message.external_id)
        if message.parent_id is not None and (parent_id := _rfc_message_id(str(message.parent.external_id))):
            # ``parent`` is the reply's In-Reply-To; References carries it too, so an
            # answer still threads when a provider rewrites this message's Message-ID.
            extra_headers["In-Reply-To"] = extra_headers["References"] = parent_id
        email = AnymailMessage(
            subject=subject,
            body=plain,
            from_email=sender,
            to=recipients["to"],
            cc=recipients["cc"],
            bcc=recipients["bcc"],
            headers=extra_headers,
        )
        if html:
            email.attach_alternative(html, "text/html")
        for part in parts:
            if part.file_id is None:
                continue
            file = part.file
            if file is None:
                raise IntegrationError("An email attachment is unavailable.")
            with file.open_stream() as stream:
                content = stream.read()
            email.attach(
                part.name or file.filename,
                content,
                str(file.mime_type or part.type or "application/octet-stream"),
            )
        return email

    def _sender(self, message: Any) -> str:
        """``Message.sender``'s address, else the source's first ``From``, else the channel default."""

        if message.sender_id is not None:
            sender = _address(message.sender)
            if sender:
                return sender
        values = _source_addresses(message, "from")
        if values:
            return values[0]
        return _mailbox(*self.outbound_sender())

    def _recipients(self, message: Any) -> dict[str, list[str]]:
        """The message's addresses per envelope role, else its source's for a role it has none in."""

        recipients: dict[str, list[str]] = {"to": [], "cc": [], "bcc": []}
        for row in message.recipients.select_related("handle").order_by("role", "pk"):
            address = _address(row.handle)
            if address:
                recipients[str(row.role)].append(address)
        for role in recipients:
            if not recipients[role]:
                recipients[role].extend(_source_addresses(message, role))
            recipients[role] = _dedupe(recipients[role])
        return recipients

    @staticmethod
    def _body(parts: list[Any], part_model: Any) -> tuple[str, str]:
        """The message's ``(plain, html)`` bodies; an HTML-only message has no plain one.

        The plain part is only ever the message's own text: it is never made from
        the HTML, so Django sends an HTML-only message without a text part.
        """

        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in parts:
            if part.role in (part_model.PartRole.TITLE, part_model.PartRole.HEADER) or part.fragment_id is None:
                continue
            text = str(part.fragment.text)
            if part.type.casefold() == "text/html":
                html_parts.append(text)
            else:
                plain_parts.append(text)
        html = "\n".join(html_parts)
        plain = "\n\n".join(plain_parts)
        return plain, html

    @staticmethod
    def _headers(parts: list[Any], part_model: Any) -> dict[str, str]:
        headers: dict[str, str] = {}
        for part in parts:
            name = str(part.name or "").strip()
            if (
                part.role != part_model.PartRole.HEADER
                or part.fragment_id is None
                or not name
                or name.casefold() in _ENVELOPE_HEADERS
                or _HEADER_NAME_RE.fullmatch(name) is None
            ):
                continue
            headers[name] = str(part.fragment.text)
        return headers


class SystemChannelBackend(AnymailEmailChannelBackend):
    """The system channel's backend: system mail is recorded on it, and nobody chooses it.

    It delivers as every email channel does. It is system-only: no person sends
    from it or watches it, so it is never selected for outbound email and never a
    workflow trigger's source.
    """

    key = SYSTEM_CHANNEL_BACKEND
    label = "System mail"
    system_only = True

    @classmethod
    def system_mail_configured(cls) -> bool:
        """Whether system mail can be sent, from settings alone: a transport mailer and a valid sender.

        True exactly when :meth:`default_sender` returns, which is when a connected
        system channel's :meth:`outbound_sender` does. It reads no row, so a
        projection may ask it per record.
        """

        try:
            cls.default_sender()
        except ValidationError:
            return False
        return True


def carries_address(address: str) -> bool:
    """Whether the transports this backend writes for can carry ``address``.

    Its part before the ``@`` must be ASCII: no transport here speaks SMTPUTF8.
    An internationalised domain is carried, written in its ASCII (IDNA) form
    (:func:`_mailbox`). :func:`require_carried` refuses what it does not carry.
    """

    local, _at, _domain = address.rpartition("@")
    return local.isascii()


def require_carried(address: str, *, key: str) -> None:
    """Refuse ``address`` under ``key``, code ``cannot_carry``, unless :func:`carries_address` holds for it.

    The one refusal for every address messaging writes for the transport: a
    sender before any mail is written (under ``channel``), a recipient of system
    mail (under its role), and a mailbox the delivery writes, whose failure says it.
    """

    if not carries_address(address):
        raise ValidationError({key: ValidationError(
            "This mail transport cannot carry %(address)s: its part before the @ is outside ASCII.",
            code="cannot_carry", params={"address": address},
        )})


class SystemMailer(BaseEmailBackend):
    """Django's default mailer wherever messaging is installed: it records each email on the system channel.

    Each email becomes one queued message through
    :meth:`~angee.messaging.managers.MessageManager.compose_as_system`, in the
    caller's transaction, so mail sent in a transaction that rolls back is never
    sent; it sends nothing itself. Delivery runs later through ``MAILERS["transport"]``.
    Called while a delivery is in progress it refuses, so no transport can hand a
    delivery back to it. A batch is recorded whole or not at all; an email with
    no recipient is skipped, as Django's mailers skip one. What an email may
    carry is the closed list :func:`_system_mail` reads.
    """

    reason = "messaging.system_mailer"
    """The named system work the recorded messages are written under."""

    def __init__(self, *, fail_silently: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_silently = fail_silently

    def send_messages(self, email_messages: Any) -> int:
        """Record each email with a recipient as one queued system message; return how many were recorded."""

        if _DELIVERING.get():
            # Never silenced: a transport that hands delivery back would record each delivery again.
            raise IntegrationError(
                'MAILERS["transport"] hands delivery back to the system mailer; point it at the mailer that sends.',
            )
        messages = list(email_messages)
        try:
            recorded = [_system_mail(email) for email in messages if email.recipients()]
            message_manager = apps.get_model("messaging", "Message").objects
            with transaction.atomic():
                for arguments in recorded:
                    message_manager.compose_as_system(reason=self.reason, **arguments)
        except (ValueError, ValidationError, DatabaseError) as error:
            if not self.fail_silently:
                raise
            logger.warning("The system mailer recorded none of %d emails (%s).", len(messages), type(error).__name__)
            return 0
        return len(recorded)


_DROPPED_HEADERS = frozenset({"date", "message-id"})
"""Headers an email may carry that system mail drops: compose mints the Message-ID, and delivery dates the mail."""


def _system_mail(email: EmailMessage) -> dict[str, Any]:
    """Return ``email`` as :meth:`compose_as_system` arguments, reading nothing; refuse what it cannot carry.

    The closed list: ``to``, ``cc``, ``bcc`` and ``subject`` are carried as
    text; a plain or HTML body, and one HTML alternative beside a plain body,
    become ``text`` and ``html``. ``Message-ID`` and ``Date`` are dropped
    (compose mints the one, delivery writes the other), ``from_email`` and
    ``encoding`` are not carried (system mail is sent from the system channel's
    sender, which replaces ``SERVER_EMAIL`` on a direct ``mail_admins`` too, and
    stored as text). Attachments, ``reply_to``, any other header, any other body
    or alternative type, and an ``AnymailMessage`` (whose provider options mean
    nothing to a recorded message) are refused with ``ValueError``; an empty
    body and a recipient the transport cannot carry are refused by
    ``compose_as_system``.
    """

    if isinstance(email, AnymailMessage):
        raise ValueError("System mail does not carry an AnymailMessage's provider options.")
    if email.attachments:
        raise ValueError("System mail carries no attachments.")
    if email.reply_to:
        raise ValueError("System mail carries no Reply-To.")
    for name in email.extra_headers:
        if name.lower() not in _DROPPED_HEADERS:
            raise ValueError(f"System mail carries no {name} header.")
    text, html = "", ""
    body = force_str(email.body or "")
    if email.content_subtype == "plain":
        text = body
    elif email.content_subtype == "html":
        html = body
    else:
        raise ValueError(f"System mail carries no text/{email.content_subtype} body.")
    alternatives = email.alternatives if isinstance(email, EmailMultiAlternatives) else []
    for content, mimetype in alternatives:
        if mimetype != "text/html" or html:
            raise ValueError(f"System mail carries one text/html alternative beside a plain body, not {mimetype}.")
        html = content.decode() if isinstance(content, bytes) else str(content)
    return {
        "to": [str(address) for address in email.to],
        "cc": [str(address) for address in email.cc],
        "bcc": [str(address) for address in email.bcc],
        "subject": str(email.subject),
        "text": text,
        "html": html,
    }


def _mailbox(name: str, address: str) -> str:
    """One mailbox as this backend writes it into a header; its display name as :func:`header_text` carries it.

    Every name the backend writes (a recipient's, a sender's, a retained source
    header's) is written here, so a name holding a literal encoded word, which
    inbound mail can store, reaches the wire as written instead of decoded into
    another name or address. An internationalised domain is written in its ASCII
    (IDNA) form, as Django writes one in the headers it builds, so every
    transport is given an address it can carry; the stored address keeps what
    was typed. A local part outside ASCII cannot be written, as no transport here
    speaks SMTPUTF8: the delivery fails with that reason.
    """

    local, at, domain = address.rpartition("@")
    try:
        require_carried(address, key="address")
    except ValidationError as refused:
        # A delivery fails with the refusal's reason; it is never a form error.
        raise IntegrationError(f"{' '.join(refused.messages)} Send to another address of this contact.") from refused
    if at and not domain.isascii():
        address = f"{local}@{punycode(domain)}"
    return formataddr((header_text(name), address))


def _address(handle: Any) -> str:
    value = str(handle.value or "").strip()
    if "@" not in value:
        return ""
    return _mailbox(str(handle.display_name or ""), value)


def _source_addresses(message: Any, header: str) -> list[str]:
    return [_mailbox(name, address) for name, address in getaddresses(message.source_header_values(header))
            if address]


def _dedupe(addresses: Any) -> list[str]:
    deduped: dict[str, str] = {}
    for address in addresses:
        if bare := _bare_address(str(address)):
            deduped.setdefault(bare, str(address))
    return list(deduped.values())


def _bare_address(address: str) -> str:
    parsed = getaddresses([address])
    return parsed[0][1].strip().casefold() if parsed else ""


def _rfc_message_id(external_id: str) -> str:
    """Return ``<external_id>`` when the id is already an RFC-5322 Message-ID, else ``""``."""

    cleaned = external_id.strip().strip("<>")
    if cleaned and "@" in cleaned and not any(char.isspace() for char in cleaned):
        return f"<{cleaned}>"
    return ""


def _message_id(external_id: str) -> str:
    """Return a stable RFC-5322 Message-ID derived from the delivery idempotency key."""

    return _rfc_message_id(external_id) or f"<{hashlib.sha256(external_id.encode('utf-8')).hexdigest()}@angee.invalid>"
