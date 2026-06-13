"""Settings fragments required by Angee integration."""

from __future__ import annotations

SETTINGS = {
    # The ``VCSIntegration.client_class`` registry: each key a ``VCSIntegration``
    # row may name → the dotted path of the ``VCSClient`` it resolves to. ``integrate``
    # is host-agnostic, so its only built-in client is the ``none`` null-object
    # (``ImplClassField`` requires a non-empty registry); a host addon adds its own
    # impl with a yamlconf dotted key (``"ANGEE_VCS_CLIENT_CLASSES.github": "…"``).
    # See ``angee.base.fields.ImplClassField``.
    "ANGEE_VCS_CLIENT_CLASSES": {"none": "angee.integrate.vcs.client.NoopVCSClient"},
}
"""Django settings contributed when the integrate addon is installed."""
