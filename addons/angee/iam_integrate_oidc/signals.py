"""Sign-in safety guard wired when the OIDC login addon is installed.

``integrate`` owns account disconnect generically and knows nothing about login.
This addon adds the one login rule it must respect: a user who can *only* sign in
through OIDC may not delete their last sign-in credential. It enforces that with a
``pre_delete`` veto on the credential, so the generic disconnect path surfaces it
as a typed error rather than stranding the user.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models.signals import pre_delete

from angee.iam_integrate_oidc.identity import is_only_oidc_sign_in
from angee.integrate.oauth.errors import OAuthFlowError


def connect() -> None:
    """Wire the last-sign-in guard onto the concrete credential model.

    Deferred via ``lazy_model_operation`` so it binds whenever the concrete
    ``integrate.Credential`` becomes available — at app population in the composed
    runtime, or later (e.g. test-defined models) in a bare settings module.
    """

    apps.lazy_model_operation(_bind_guard, ("integrate", "credential"))


def _bind_guard(credential_model: Any) -> None:
    """Connect the last-sign-in guard to the resolved credential model."""

    pre_delete.connect(guard_last_sign_in, sender=credential_model, dispatch_uid="iam_integrate_oidc.last_sign_in")


def guard_last_sign_in(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Veto deleting a user's last OIDC sign-in credential."""

    del sender, kwargs
    if str(instance.kind) != "oauth":
        return
    oauth_client = getattr(instance, "oauth_client", None)
    if oauth_client is None or not getattr(oauth_client, "login_enabled", False):
        return
    if is_only_oidc_sign_in(instance.user):
        raise OAuthFlowError("only_sign_in_method", 409)
