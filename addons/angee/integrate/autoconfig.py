"""Settings fragments required by Angee integration."""

from __future__ import annotations

SETTINGS = {
    # The ``VCSIntegration.backend_class`` registry: each key a ``VCSIntegration``
    # row may name → the dotted path of the ``VCSBackend`` it resolves to. ``integrate``
    # is host-agnostic, so its built-ins are the ``none`` null-object (``ImplClassField``
    # requires a non-empty registry) and ``local``, which inventories a local working
    # tree with no network (dev/offline template + skill discovery); a host addon adds
    # its own impl with a yamlconf dotted key (``"ANGEE_VCS_BACKEND_CLASSES.github": "…"``).
    # See ``angee.base.fields.ImplClassField``.
    "ANGEE_VCS_BACKEND_CLASSES": {
        "none": "angee.integrate.vcs.backend.NoopVCSBackend",
        "local": "angee.integrate.vcs.backend.LocalVCSBackend",
    },
}
"""Django settings contributed when the integrate addon is installed."""
