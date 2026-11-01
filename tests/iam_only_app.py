"""Native source IAM config without provenance or presentation fixtures."""

from angee.iam.apps import IAMConfig


class IAMOnlyConfig(IAMConfig):
    def import_models(self):
        super().import_models()
        import tests.iam_only_models  # noqa: F401
