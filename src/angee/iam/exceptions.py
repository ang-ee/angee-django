"""Public-safe IAM account failures."""

from rebac import MissingActorError

from angee.base.exceptions import DomainError, ErrorClassification
from angee.base.exceptions import classify_error as classify_domain_error


class AccountExists(DomainError):
    """A person email is already claimed; never carries an account's details."""

    code = "ACCOUNT_EXISTS"


class AmbiguousAccountEmail(DomainError):
    """Multiple person accounts claim one email; carries no account details."""

    code = "ACCOUNT_EMAIL_AMBIGUOUS"


def classify_error(error: BaseException | None) -> ErrorClassification:
    """Classify native IAM authentication failures before generic domain errors."""
    if isinstance(error, MissingActorError):
        return ErrorClassification("UNAUTHENTICATED", "Authentication required.")
    return classify_domain_error(error)
