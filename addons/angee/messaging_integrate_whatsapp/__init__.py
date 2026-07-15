"""WhatsApp channel backend for the messaging addon.

Contributes the ``whatsapp`` key into ``ANGEE_CHANNEL_BACKEND_CLASSES`` so a
``messaging.Channel`` can link a personal WhatsApp account: QR-code device
pairing and live message sync run inside a long-lived session task on the
dedicated ``whatsapp`` Celery queue (:mod:`.client`, :mod:`.tasks`), and a
WhatsApp iOS device backup imports through ``manage.py whatsapp_import``
(:mod:`.backup`). :mod:`.parser` owns the identity rules — JID normalization
and the chat-scoped external ids — shared by both paths, so a backup import
and live sync converge on the same rows; the idempotent map onto threads,
parts, fragments, and storage files is owned by ``Message.objects.ingest`` in
``angee.messaging``.
"""

from __future__ import annotations
