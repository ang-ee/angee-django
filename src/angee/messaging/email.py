"""Vendor-neutral outbound email delivery through django-anymail."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from email.utils import formataddr, getaddresses, parseaddr
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
from django.core.mail import mailers
from django.core.mail.backends.base import BaseEmailBackend
from django.core.validators import validate_email
from django.utils.encoding import punycode
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ConnectTimeout
from urllib3.exceptions import MaxRetryError, NewConnectionError

from angee.base.serialization import strip_null_bytes
from angee.integrate.errors import IntegrationError, is_transient_status
from angee.messaging.backends import ChannelBackend, DeliveryOutcome, DeliveryUncertain, OutboundEmailNotConfigured

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


class AnymailEmailChannelBackend(ChannelBackend):
    """Send an email channel's outbound messages through Django's Anymail backend.

    The deployment selects the concrete ESP with Django's ``MAILERS["default"]`` and supplies
    its vendor settings through ``ANYMAIL``/``ANYMAIL_*``. This adapter remains
    provider-neutral and also works with Anymail's deterministic test backend.
    """

    key = "email"
    label = "Email"
    icon = "mail"

    @classmethod
    def outbound_email_configured(cls) -> bool:
        """Read the deployment's native email transport configuration decision."""

        return bool(settings.ANGEE_EMAIL_DELIVERY_CONFIGURED)

    def outbound_sender(self) -> tuple[str, str]:
        """Resolve and validate this channel's configured default envelope sender.

        The channel's ``from_email`` config wins over ``DEFAULT_FROM_EMAIL``. An
        unconfigured transport raises :class:`OutboundEmailNotConfigured`; an invalid
        address is refused under ``channel`` too.
        """

        if not self.outbound_email_configured():
            raise OutboundEmailNotConfigured
        config = self.bridge.config if isinstance(self.bridge.config, dict) else {}
        name, address = parseaddr(str(config.get("from_email") or settings.DEFAULT_FROM_EMAIL))
        try:
            validate_email(address)
        except ValidationError as error:
            raise ValidationError({"channel": "Configure a valid sender address for this email channel."}) from error
        return name, address

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
        connection = mailers.default
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
    if at and not local.isascii():
        raise IntegrationError(
            f"{address} cannot be delivered: this mail transport cannot carry an address whose part before "
            "the @ is outside ASCII. Send to another address of this contact.",
        )
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
