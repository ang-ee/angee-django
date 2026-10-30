"""Native source test app and its fixture-provider dependencies."""

from django.apps import AppConfig


class TestsConfig(AppConfig):
    """Contribute fixture providers after their production registry owners."""

    name = "tests"
    requires_apps = (
        "angee.integrate_vcs",
        "angee.agents",
        "angee.messaging",
        "angee.posts",
        "angee.extraction",
    )
