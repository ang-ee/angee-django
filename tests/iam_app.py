"""Test IAM AppConfig that installs the bare-harness concrete user model."""

from __future__ import annotations

from angee.iam.apps import IAMConfig


class TestIAMConfig(IAMConfig):
    """IAM app config used by source-addon tests."""

    def import_models(self) -> None:
        """Import source IAM models, then the concrete test user model."""

        super().import_models()
        import tests.iam_models  # noqa: F401
