"""Public-safe IAM account failures."""

from angee.base.exceptions import DomainError


class AccountExists(DomainError):
    """A person email is already claimed; never carries an account's details."""

    code = "ACCOUNT_EXISTS"


class AmbiguousAccountEmail(DomainError):
    """Multiple person accounts claim one email; carries no account details."""

    code = "ACCOUNT_EMAIL_AMBIGUOUS"
