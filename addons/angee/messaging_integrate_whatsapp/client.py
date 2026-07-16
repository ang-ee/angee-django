"""WhatsApp-specific live-session exceptions."""

from __future__ import annotations


class DuplicateAccountRejected(Exception):
    """The scanned WhatsApp account is already claimed by another channel."""
