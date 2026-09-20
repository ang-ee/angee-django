"""OIDC identity failure codes and public messages."""

from angee.integrate.oauth.errors import OAuthFlowError

IDENTITY_RESOLUTION_FAILED = "identity_resolution_failed"
ONLY_SIGN_IN_METHOD = "only_sign_in_method"

_PUBLIC_MESSAGES = {
    IDENTITY_RESOLUTION_FAILED: "The sign-in identity could not be resolved.",
    ONLY_SIGN_IN_METHOD: "This is your only sign-in method.",
}


class IdentityFlowError(OAuthFlowError):
    """OIDC identity failure with child-addon-owned public text."""

    @property
    def public_message(self) -> str:
        """Return the stable identity message owned by this addon."""

        return _PUBLIC_MESSAGES.get(self.code, super().public_message)
