"""Django config for Angee's GitHub VCS-client addon."""

from __future__ import annotations

from django.apps import AppConfig


class IntegrateGithubConfig(AppConfig):
    """Source app manifest for the GitHub VCS client.

    Carries no models, schema, or permissions of its own: it contributes the
    :class:`~angee.integrate_github.client.GitHubClient` into
    ``ANGEE_VCS_CLIENT_CLASSES`` (via ``autoconfig``), named per
    ``VCSIntegration`` row.
    """

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.integrate_github"
    label = "integrate_github"
    depends_on = ("angee.integrate",)
