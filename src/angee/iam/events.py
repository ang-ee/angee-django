"""Transactional identity events emitted by IAM's person factory and account verbs."""

from django.dispatch import Signal

person_created = Signal()
"""Sent inside account creation with ``sender``, the new user ``instance`` and ``identity``.

Both ``create_person`` and ``create_person_as_system`` send it exactly once per
successful insert. Dependent addons may create identity links in the same
transaction; a receiver failure rolls back the account. ``identity`` is the
existing identity record a system ingress resolved for the person, else
``None``; the receiver owning identity links adopts it. Trusted ``create_user``
and bootstrap paths do not emit this event.
"""

sign_in_link_sent = Signal()
"""Sent once Django's mailer has accepted a sign-in link, with ``sender``, ``instance`` and ``link``.

``instance`` is the account the link signs in and ``link`` its ``SignInLink``
row, already stamped with ``sent_at``; ``link.created_by`` is the issuer, or
``None`` for a system issue. The token is never part of the event. It fires
after the issuing transaction committed, so a receiver records the send in its
own transaction.
"""
