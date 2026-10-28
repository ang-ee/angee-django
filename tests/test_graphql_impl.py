"""Implementation metadata projects defaults into the form's public value domain."""

import pytest
from rebac import system_context

from angee.integrate.schema import ConsoleImplChoicesQuery
from angee.integrate_github.backend import GitHubBackend
from angee.messaging.testing.models import Channel  # noqa: F401 -- register the channel metadata owner
from tests.conftest import (
    Vendor,
    addon_schema,
    create_platform_admin,
    create_user,
    execute_schema,
    result_data,
)
from tests.test_agents import InferenceProvider  # noqa: F401 -- register the provider metadata owner


class ScopedDefaultBackend(GitHubBackend):
    """A registry default whose related row may be missing or unreadable."""

    defaults = {"vendor": "impl-default-vendor", "display_name": "Literal default"}


def choices(model, actor):
    schema = addon_schema({"console": {"query": [ConsoleImplChoicesQuery]}}, "console")
    result = execute_schema(schema, """query($model: String!) {
      impl_choices(model: $model, field: "backendClass") { key defaults }
    }""", {"model": model}, user=actor)
    return result_data(result)["impl_choices"]


@pytest.mark.parametrize("model,key", [
    ("agents.InferenceProvider", "openai"),
    ("integrate_vcs.VcsBridge", "github"),
    ("messaging.Channel", "slack"),
])
def test_relation_defaults_use_public_ids_for_every_impl_consumer(composed_tables, model, key):
    admin = create_platform_admin(f"impl-default-{key}")
    with system_context(reason="test implementation default catalogue"):
        vendor = Vendor.objects.create(slug=key, display_name=key.title())

    choice = next(choice for choice in choices(model, admin) if choice["key"] == key)

    assert choice["defaults"]["vendor"] == vendor.public_id
    assert choice["defaults"]["vendor"] != key
    if key == "openai":
        assert choice["defaults"]["name"] == "OpenAI"


@pytest.mark.parametrize("target_exists,readable", [(False, True), (True, False), (True, True)])
def test_relation_default_metadata_omits_missing_and_unreadable_targets(
    composed_tables, settings, monkeypatch, target_exists, readable,
):
    settings.ANGEE_VCS_BACKEND_CLASSES = {"github": f"{__name__}.ScopedDefaultBackend"}
    actor = create_platform_admin("impl-default-admin") if readable else create_user("impl-default-reader")
    # Authorizing registry metadata does not grant access to its referenced rows.
    monkeypatch.setattr("angee.integrate.schema.can_read_impl_choices", lambda model, field, actor: True)
    vendor = None
    if target_exists:
        with system_context(reason="test scoped implementation default"):
            vendor = Vendor.objects.create(slug="impl-default-vendor", display_name="Hidden catalogue row")

    choice = choices("integrate_vcs.VcsBridge", actor)[0]

    expected = {"display_name": "Literal default", "config": {"github_api_base": "https://api.github.com"}}
    if target_exists and readable:
        expected["vendor"] = vendor.public_id
    assert choice["defaults"] == expected
    assert ScopedDefaultBackend.effective_defaults()["vendor"] == "impl-default-vendor"
