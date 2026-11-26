"""Concrete sign-in link model for the bare-Django test runtime."""

from __future__ import annotations

from angee.iam_messaging.models import SignInLink as AbstractSignInLink


class SignInLink(AbstractSignInLink):
    """Concrete sign-in link used by tests without running the composer."""

    class Meta(AbstractSignInLink.Meta):
        """Django model options for the canonical test sign-in link."""

        abstract = False
        app_label = "iam_messaging"
        db_table = "test_iam_messaging_sign_in_link"
