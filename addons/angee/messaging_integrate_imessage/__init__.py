"""SMS + iMessage import from an unencrypted iPhone backup.

Apple Messages stores regular SMS *and* iMessage in one SQLite database in the
device backup — ``HomeDomain`` / ``Library/SMS/sms.db`` — so this addon is the
WhatsApp iPhone-backup addon with a different store schema and phone/email
identity instead of JIDs. The bottom of the stack is already generic and stays
untouched: :class:`angee.integrate_iphone.backup.IosBackup` resolves backup
blobs, the neutral messaging seam owns the idempotent map (threads, parts,
fragments, storage files, dedup), and :mod:`angee.messaging.backup_ingest` owns
the batching + resume-watermark loop. Only :mod:`.store` (the ``sms.db`` reader),
:mod:`.attributed_body` (the ``NSAttributedString`` text decoder), and
:mod:`.parser` (phone/email identity) are platform-specific.

Backup/mount import only — there is no live session, pairing, or OAuth: an Apple
account is not linkable the way a WhatsApp device is. The ``imessage`` channel
backend (:mod:`.backend`) exists only so a ``messaging.Channel`` can name this
platform as its import target.
"""

from __future__ import annotations
