"""GraphQL contributions of emailed sign-in links: the public redemption and the console's issue action."""

from __future__ import annotations

from typing import Any, cast

import strawberry
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from rebac import system_context
from strawberry_django.utils.requests import get_request

from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.ids import PublicID
from angee.iam.auth import SESSION_BACKEND, authenticate_once
from angee.iam.schema import LoginPayload, UserType
from angee.iam_messaging.auth import sign_in_credentials
from angee.iam_messaging.models import ISSUE_SIGN_IN_LINK, SIGN_IN_LINK_QUEUED_MESSAGE

User = cast(type[Any], get_user_model())


@strawberry.type
class SignInLinkMutation:
    """The session sign-in a link opens."""

    @strawberry.mutation
    def redeem_sign_in_link(self, info: strawberry.Info, token: str) -> LoginPayload:
        """Sign in with a single-use sign-in link and bind the person to the session.

        The token authenticates through ``authenticate()``, so django-axes
        throttles failed links as it does passwords; like ``login``, it is the
        request's only sign-in attempt. The session records IAM's
        ``SESSION_BACKEND``, not the link's backend. Every refusal is the same
        ``ok: false``, whatever the reason.
        """

        request = get_request(info)
        # As for passwords: the anonymous request's sign-in writes are the system work it sanctions.
        with system_context(reason="iam_messaging.sign_in_link.redeem"):
            user = authenticate_once(request, **sign_in_credentials(token))
            if user is None:
                return LoginPayload(ok=False)
            auth_login(request, user, backend=SESSION_BACKEND)
        return LoginPayload(ok=True, user=cast(UserType, user))


@strawberry.type
class SignInLinkAccountMutation:
    """The account action that emails a sign-in link, gated by the target user's ``issue_sign_in_link``."""

    @strawberry.mutation
    @action_guard("Sending the sign-in link failed.")
    def issue_user_sign_in_link(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Email the account a single-use sign-in link after the account owner's checks; the link is never returned.

        Success means recorded and queued, not delivered: the answer says so, and the mail's delivery shows on the
        system channel.
        """

        user = authorized_permission_target(info, User, id, ISSUE_SIGN_IN_LINK)
        user.issue_sign_in_link()
        return ActionResult(ok=True, message=SIGN_IN_LINK_QUEUED_MESSAGE)


schemas = {
    "public": {"mutation": [SignInLinkMutation]},
    "console": {"mutation": [SignInLinkMutation, SignInLinkAccountMutation]},
}
"""GraphQL contributions installed by the emailed sign-in link addon."""
