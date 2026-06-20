"""Django config for Angee's GitHub VCS-backend addon."""

from __future__ import annotations

from django.apps import AppConfig


class IntegrateGithubConfig(AppConfig):
    """Source app manifest for the GitHub VCS backend.

    Carries no models, schema, or permissions of its own: it contributes the
    :class:`~angee.integrate_github.backend.GitHubBackend` into
    ``ANGEE_VCS_BACKEND_CLASSES`` (via ``autoconfig``), named per
    ``VcsBridge.backend_class`` row.
    """

    default = True
    angee_addon = True
    name = "angee.integrate_github"
    label = "integrate_github"
    depends_on = ("angee.integrate",)
