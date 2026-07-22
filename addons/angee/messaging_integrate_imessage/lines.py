"""Resolve a message's **local line** — the user's own number/address that handled it.

Apple stores the handling line per message in ``message.destination_caller_id``. It
is messy: the same number appears as ``+14244798217``, ``14244798217``, and
``tel:+14244798217``; iMessage addresses are emails (``alexis@ww.net``); and rows
with no line carry a random account GUID or ``NULL``/``''``. :func:`normalize_line`
collapses those variants to one canonical key so the importer can route one channel
per line, or ``None`` for "no resolvable line" (the catch-all bucket).

Phone parsing uses ``phonenumbers`` — the stack's phone library, the same one
:meth:`angee.parties.models.Handle.normalize_value` parses handles with — but the
line rule differs on purpose: an unresolvable value is ``None`` here (not a
digit-only fallback), a leading ``tel:`` URI scheme is stripped, and a plain digit
run is assumed country-coded so E.164 validation can reject GUIDs and short codes.
"""

from __future__ import annotations

from phonenumbers import (
    NumberParseException,
    PhoneNumberFormat,
    format_number,
    is_possible_number,
    is_valid_number,
    parse,
)

_TEL_PREFIX = "tel:"
"""The RFC 3966 telephone URI scheme Apple sometimes prefixes onto a line."""

_MIN_COUNTRY_CODED_DIGITS = 7
"""A plus-less digit run at least this long is assumed already country-coded."""


def normalize_line(raw: str | None) -> str | None:
    """Return the canonical local-line key for one ``destination_caller_id``.

    ``None`` means "no resolvable line" — the catch-all bucket — for empty/NULL
    values, account GUIDs, short codes, and anything ``phonenumbers`` rejects. An
    ``@`` routes to the lowercased email line; otherwise the value is treated as a
    phone: a leading ``tel:`` is stripped, digits (with a leading ``+``, prepended
    when absent on a country-coded-length run) are parsed, and a possible + valid
    number formats to E.164 so the three variants of one number converge.
    """

    value = (raw or "").strip()
    if not value:
        return None
    if value.lower().startswith(_TEL_PREFIX):
        value = value[len(_TEL_PREFIX) :].strip()
    if "@" in value:
        return value.lower()
    digits = "".join(character for character in value if character.isdigit())
    if not digits:
        return None
    if value.startswith("+"):
        candidate = f"+{digits}"
    elif len(digits) >= _MIN_COUNTRY_CODED_DIGITS:
        candidate = f"+{digits}"
    else:
        candidate = digits
    try:
        number = parse(candidate, None)
    except NumberParseException:
        return None
    if is_possible_number(number) and is_valid_number(number):
        return format_number(number, PhoneNumberFormat.E164)
    return None


def line_channel_name(line: str | None) -> str:
    """Return the channel display name for a local line (``None`` → catch-all)."""

    return f"Messages {line}" if line else "Messages (other)"
