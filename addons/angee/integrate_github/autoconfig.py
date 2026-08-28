"""Settings fragments required by the GitHub VCS implementation addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the GitHub backend into the VCS bridge backend registry. Dotted
    # key so it merges, not replaces.
    "ANGEE_VCS_BACKEND_CLASSES.github": "angee.integrate_github.backend.GitHubBackend",
}
"""Django settings contributed when the GitHub VCS implementation addon is installed."""
