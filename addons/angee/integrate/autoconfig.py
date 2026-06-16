"""Settings fragments required by Angee integration."""

from __future__ import annotations

SETTINGS = {
    # OAuth connection substrate. Host-provided OAuth client registrations (secrets
    # included) are declared here and synced by ``manage.py oauth_clients``; the
    # public catalogue is seeded from install-tier resources instead. The TTL bounds
    # the single-use redirect state record (shared by connect and OIDC login). OIDC
    # discovery TTL belongs to the ``iam_integrate_oidc`` addon.
    "ANGEE_INTEGRATE_OAUTH_CLIENTS": (),
    "ANGEE_INTEGRATE_OAUTH_STATE_TTL": 600,
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
