"""Transactional identity events emitted by IAM's person factory."""

from django.dispatch import Signal

person_created = Signal()
"""Sent inside account creation with ``sender`` and the new user ``instance``.

Both ``create_person`` and ``create_person_as_system`` send it exactly once per
successful insert. Dependent addons may create identity links in the same
transaction; a receiver failure rolls back the account. Trusted ``create_user``
and bootstrap paths do not emit this event.
"""
