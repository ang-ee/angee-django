"""Source model for the OIDC login addon: the OIDC refinement of an OAuth client.

The clean data-layer extension: an ``integrate.OAuthClient`` that also speaks
OpenID Connect has one of these (``oauth_client.oidc``), and its presence is what
makes a provider a *login* provider. ``integrate`` owns OAuth and never references
this model; OIDC — the model here, the protocol in :mod:`angee.iam_integrate_oidc.
protocol`, and the login flow — lives entirely in this addon.
"""

from __future__ import annotations

from django.db import models
from rebac.managers import RebacManager

from angee.base.fields import SqidField
from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeModel


class OidcClient(SqidMixin, AuditMixin, AngeeModel):
    """The OpenID Connect refinement of an ``integrate.OAuthClient``.

    Composition over inheritance at the data layer (the protocol uses real class
    inheritance): it owns what verifying an ID token needs — discovery and the
    issuer/JWKS — on top of the OAuth base (which already owns the userinfo endpoint
    and claim mapping). It also holds the per-provider login *policy* (whether a
    login may link to or create a user, and the email-domain allow-list); the OAuth
    base stays free of all of it, so account-connect carries no login logic.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="oic", min_length=8)
    oauth_client = models.OneToOneField(
        "integrate.OAuthClient",
        on_delete=models.CASCADE,
        related_name="oidc",
    )
    issuer = models.URLField(blank=True)
    discovery_url = models.URLField(blank=True)
    jwks_uri = models.URLField(blank=True)
    link_on_email_match = models.BooleanField(default=False)
    create_on_login = models.BooleanField(default=False)
    allowed_email_domains = models.JSONField(default=list, blank=True)

    objects = RebacManager()

    class Meta:
        """Django model options for OIDC client refinements."""

        abstract = True
        ordering = ("oauth_client__slug",)
        rebac_resource_type = "iam_integrate_oidc/oidc_client"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the underlying OAuth client's label."""

        return f"oidc:{getattr(self.oauth_client, 'slug', '?')}"

    @property
    def allowed_email_domain_values(self) -> list[str]:
        """Return the login domain allow-list as strings."""

        value = self.allowed_email_domains
        if not isinstance(value, (list, tuple)):
            return []
        return [str(item) for item in value]

    def allows_email_domain(self, email: str | None) -> bool:
        """Return whether ``email`` is allowed by this provider's login domain policy."""

        allowed_domains = {
            domain.strip().lower()
            for domain in self.allowed_email_domain_values
            if domain.strip()
        }
        if not allowed_domains:
            return True
        if not email or "@" not in email:
            return False
        return email.rsplit("@", 1)[1].lower() in allowed_domains
