"""Public event seams emitted by messaging write owners."""

from __future__ import annotations

from django.dispatch import Signal

message_ingested = Signal()
"""Sent inside the landing transaction for each new message activity.

Receivers get ``sender`` (the concrete Message model) and ``instance``. A
provider replay whose row and thread are unchanged emits nothing; a newly
created internal/external message, or an existing provider message newly moved
onto a thread, emits once. Composition addons may react transactionally without
being named by messaging.
"""
