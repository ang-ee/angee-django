"""OIDC login fields contributed onto ``integrate.OAuthClient``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import models

from angee.base.models import AngeeModel


class OAuthClientOidc(AngeeModel):
    """OpenID Connect login extension for ``integrate.OAuthClient``.

    The composer folds these fields into the single OAuth client table when the
    OIDC login addon is installed. Presence is no longer a separate 1:1 row:
    ``login_enabled`` is the discriminator for public login/link flows.
    """

    extends = "integrate.OAuthClient"

    issuer = models.URLField(blank=True)
    jwks_uri = models.URLField(blank=True)
    login_enabled = models.BooleanField(default=False, db_index=True)
    link_on_email_match = models.BooleanField(default=False)
    create_on_login = models.BooleanField(default=False)
    allowed_email_domains = models.JSONField(default=list, blank=True)

    class Meta:
        """Abstract extension base composed into ``integrate.OAuthClient``."""

        abstract = True

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

    DISCOVERY_FIELDS = {
        "issuer": "issuer",
        "jwks_uri": "jwks_uri",
    }
    """Discovery-document keys projected onto OIDC login fields."""

    def fill_extension_fields_from_discovery(self, discovery: Mapping[str, Any]) -> bool:
        """Fill blank OIDC fields from a discovery document; return whether changed."""

        changed = False
        for field, key in self.DISCOVERY_FIELDS.items():
            if getattr(self, field, ""):
                continue
            value = discovery.get(key)
            if value:
                setattr(self, field, str(value))
                changed = True
        parent = getattr(super(), "fill_extension_fields_from_discovery", None)
        return (bool(parent(discovery)) if parent is not None else False) or changed
