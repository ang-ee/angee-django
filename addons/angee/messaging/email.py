"""Vendor-neutral outbound email delivery through django-anymail."""

from __future__ import annotations

import hashlib
import logging
import re
from email.utils import formataddr, getaddresses
from typing import Any

from anymail.message import AnymailMessage
from django.apps import apps
from django.conf import settings
from django.utils.html import strip_tags

from angee.messaging.backends import ChannelBackend, ParsedMessage

logger = logging.getLogger(__name__)

_ENVELOPE_HEADERS = frozenset({"bcc", "cc", "from", "subject", "to"})
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")


class AnymailEmailChannelBackend(ChannelBackend):
    """Send an email channel's outbound messages through Django's Anymail backend.

    The deployment selects the concrete ESP with ``EMAIL_BACKEND`` and supplies
    its vendor settings through ``ANYMAIL``/``ANYMAIL_*``. This adapter remains
    provider-neutral and also works with Anymail's deterministic test backend.
    """

    key = "email"
    label = "Email"
    icon = "mail"

    def fetch_messages(self) -> list[ParsedMessage]:
        """Return no messages; an outbound-only email channel has no poll source."""

        return []

    def deliver(self, message: Any) -> bool:
        """Render and send ``message`` when an email transport is configured."""

        if not bool(getattr(settings, "ANGEE_EMAIL_DELIVERY_CONFIGURED", False)):
            logger.warning(
                "Outbound email delivery is not configured; message %s was not sent.",
                getattr(message, "pk", None),
            )
            return False
        email = self.email_message(message)
        if not email.from_email or not (email.to or email.cc or email.bcc):
            logger.warning(
                "Outbound email message %s has no complete from/to envelope; it was not sent.",
                getattr(message, "pk", None),
            )
            return False
        accepted = email.send()
        if not accepted:
            logger.warning("The configured email backend declined message %s.", getattr(message, "pk", None))
        return bool(accepted)

    def email_message(self, message: Any) -> AnymailMessage:
        """Render a messaging ``Message`` into Anymail's provider-neutral message."""

        part_model = apps.get_model("messaging", "Part")
        parts = part_model.objects.reading_order_for_message(message)
        participants = list(message.participants.select_related("handle").order_by("role", "pk"))
        sender = self._sender(message, participants)
        recipients = self._recipients(message, participants)
        subject = next(
            (
                part.fragment.text
                for part in parts
                if part.role == part_model.PartRole.TITLE and part.fragment_id is not None
            ),
            "",
        )
        plain, html = self._body(parts, part_model)
        extra_headers = self._headers(parts, part_model)
        if message.external_id:
            extra_headers["Message-ID"] = _message_id(str(message.external_id))
            extra_headers["X-Angee-External-ID"] = str(message.external_id)
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
            with part.file.open_stream() as stream:
                content = stream.read()
            email.attach(
                part.name or part.file.filename,
                content,
                str(part.file.mime_type or part.type or "application/octet-stream"),
            )
        return email

    def _sender(self, message: Any, participants: list[Any]) -> str:
        if message.sender_id is not None:
            sender = _address(message.sender)
            if sender:
                return sender
        for participant in participants:
            if str(participant.role) == "from":
                sender = _address(participant.handle)
                if sender:
                    return sender
        values = _metadata_addresses(message, "from")
        if values:
            return values[0]
        config = self.bridge.config if isinstance(self.bridge.config, dict) else {}
        return str(config.get("from_email") or getattr(settings, "DEFAULT_FROM_EMAIL", ""))

    def _recipients(self, message: Any, participants: list[Any]) -> dict[str, list[str]]:
        recipients = {"to": [], "cc": [], "bcc": []}
        for participant in participants:
            role = str(participant.role)
            if role not in recipients:
                continue
            address = _address(participant.handle)
            if address:
                recipients[role].append(address)
        for role in recipients:
            if not recipients[role]:
                recipients[role].extend(_metadata_addresses(message, role))
            recipients[role] = _dedupe(recipients[role])
        return recipients

    @staticmethod
    def _body(parts: list[Any], part_model: Any) -> tuple[str, str]:
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
        return plain or strip_tags(html), html

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


def _address(handle: Any) -> str:
    value = str(getattr(handle, "value", "") or "").strip()
    if "@" not in value:
        return ""
    return formataddr((str(getattr(handle, "display_name", "") or "").strip(), value))


def _metadata_addresses(message: Any, header: str) -> list[str]:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    raw_headers = metadata.get("headers")
    if not isinstance(raw_headers, dict):
        return []
    values: Any = next((value for name, value in raw_headers.items() if str(name).casefold() == header), ())
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, list | tuple):
        return []
    return [formataddr((name, address)) for name, address in getaddresses([str(value) for value in values]) if address]


def _dedupe(addresses: Any) -> list[str]:
    deduped: dict[str, str] = {}
    for address in addresses:
        if bare := _bare_address(str(address)):
            deduped.setdefault(bare, str(address))
    return list(deduped.values())


def _bare_address(address: str) -> str:
    parsed = getaddresses([address])
    return parsed[0][1].strip().casefold() if parsed else ""


def _message_id(external_id: str) -> str:
    """Return a stable RFC-5322 Message-ID derived from the delivery idempotency key."""

    cleaned = external_id.strip().strip("<>")
    if cleaned and "@" in cleaned and not any(char.isspace() for char in cleaned):
        return f"<{cleaned}>"
    digest = hashlib.sha256(external_id.encode("utf-8")).hexdigest()
    return f"<{digest}@angee.invalid>"
