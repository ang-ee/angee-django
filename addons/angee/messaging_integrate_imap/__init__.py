"""IMAP email channel backend for the messaging addon.

Contributes the ``imap`` key into ``ANGEE_CHANNEL_BACKEND_CLASSES`` so a
``messaging.Channel`` can sync a mailbox account (Gmail, Fastmail, self-hosted —
anything speaking IMAP4rev1) into threads and messages. The backend owns only
transport (:mod:`.backend`) and MIME parsing (:mod:`.parser`); the idempotent map
onto threads, parts, fragments, and storage files is owned by
``Message.objects.ingest`` in ``angee.messaging``.
"""

from __future__ import annotations
