"""Lifecycle seams for prerequisites owned by other addons."""

from django.dispatch import Signal

pre_load = Signal()
"""Validate owner-created identities referenced by a selected resource batch.

The sender is the concrete ledger model. ``referenced_handles`` contains
canonical ``(addon_name, xref)`` pairs. Sent once inside the import transaction,
including dry runs; this event does not adopt or create prerequisite records.
"""

post_load = Signal()
"""Validate referenced identities after all rows and grants, inside the same transaction."""
