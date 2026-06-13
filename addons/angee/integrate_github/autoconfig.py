"""Settings fragments required by the GitHub VCS-client addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the GitHub client into the host-agnostic registry the
    # ``integrate`` addon declares. A ``VCSIntegration`` row selects it with
    # ``client_class = "github"``. Dotted key so it merges, not replaces.
    "ANGEE_VCS_CLIENT_CLASSES.github": "angee.integrate_github.client.GitHubClient",
}
"""Django settings contributed when the GitHub VCS-client addon is installed."""
