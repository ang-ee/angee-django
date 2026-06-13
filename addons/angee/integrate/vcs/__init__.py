"""Host-agnostic VCS inventory support for the integrate addon.

Holds the model-free git-client contract (:mod:`client`) and the template-manifest
parser (:mod:`templates`). It owns *enumeration over a host's REST API*; it never
clones — git transport (clone/fetch/worktree) is the operator's job. Host-specific
clients (e.g. GitHub) live in their own addon and subclass
:class:`~angee.integrate.vcs.client.VCSClient`, registered into
``ANGEE_VCS_CLIENT_CLASSES`` and named per ``VCSIntegration`` row by an
``ImplClassField``.
"""
