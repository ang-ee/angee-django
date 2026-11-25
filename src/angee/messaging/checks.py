"""Messaging's sealed permissions, checked by the base sealed-permission check."""

from __future__ import annotations

from functools import partial

from angee.base.checks import check_sealed_permissions

DERIVED_READS = {"messaging/thread": ("read", "read_inbox"), "messaging/message": ("read", "read_inbox")}
"""The reads messaging derives from its extension points, which fragments never extend.

Fragments widen ``messaging/thread#read_granted`` with grants that hold for any
conversation, which both reads take, and ``messaging/thread#chatter_read`` with a
record's read of its own chatter, which only ``read`` takes; message grants go to
``messaging/message#read_granted``. An arm unioned straight into a derived read
reaches one of the two reads and not the other: the inbox would drop a grant
generic reads keep, or keep one they refuse.
"""

SEALED = {"messaging/channel": ("read", "reply", "write", "annotate", "delete")}
"""The channel permissions that subtract the system channel's seal, which fragments never extend.

Each admits administrators alone on the system channel (backend ``system``), so
an arm unioned into one would reach that channel's mail past the seal. Fragments
widen ``integrate/integration#read`` and ``#write`` instead, which the seal
subtracts.
"""

check_derived_reads = partial(check_sealed_permissions, "messaging", {**DERIVED_READS, **SEALED}, "messaging.E003")
"""``messaging.E003``: the derived thread and message reads, and the channel permissions the system seal guards."""
