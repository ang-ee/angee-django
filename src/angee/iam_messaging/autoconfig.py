"""Settings fragments contributed by the emailed sign-in link addon."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "AUTHENTICATION_BACKENDS:append": list[str],
        "ANGEE_IAM_MESSAGING_SIGN_IN_LINK_TTL": int,
        "ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN": int,
    },
)

SETTINGS: _Settings = {
    # After IAM's axes and ModelBackend entries, so axes sees every link attempt.
    "AUTHENTICATION_BACKENDS:append": ["angee.iam_messaging.auth.SignInLinkBackend"],
    # Seconds a sign-in link stays valid: Django's default for an emailed
    # password-reset link, since the person cannot request a fresh one.
    "ANGEE_IAM_MESSAGING_SIGN_IN_LINK_TTL": 259_200,
    # Seconds before an account may be sent another sign-in link.
    "ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN": 60,
}
"""Django settings contributed when the emailed sign-in link addon is installed."""
