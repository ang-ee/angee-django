"""Tests for the agents addon — skill discovery, inference model sync, and the
``SKILL.md`` parser.

Skill discovery reuses the integrate VCS inventory: the concrete
``VCSIntegration``/``Repository``/``Source`` models and the ``stub`` backend live in
``tests.test_integrate_vcs``/``tests.conftest``, so this module imports them (a
second concrete ``Source`` for ``app_label="integrate"`` would collide in the
registry) and declares only the agents concretes. Inference sync rides on the
``stub`` ``InferenceBackend`` whose canned models ride on ``provider.config``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.agents.models import Agent as AbstractAgent
from angee.agents.models import InferenceModel as AbstractInferenceModel
from angee.agents.models import InferenceProvider as AbstractInferenceProvider
from angee.agents.models import Skill as AbstractSkill
from angee.agents.skills import parse_skill_meta
from angee.iam.credentials import CredentialKind
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Credential,
    OAuthClient,
    _create_missing_tables,
    make_integration,
)
from tests.test_integrate_vcs import (
    REPOS,
    VCS_TEST_MODELS,
    Repository,
    Source,
    _vcs_integration,
)


class Skill(AbstractSkill):
    """Concrete skill used by the agents discovery tests."""

    class Meta(AbstractSkill.Meta):
        """Django model options for the canonical test skill."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_skill"
        rebac_resource_type = "agents/skill"
        rebac_id_attr = "sqid"


class InferenceProvider(AbstractInferenceProvider):
    """Concrete inference provider (capability over an integration) used by tests."""

    class Meta(AbstractInferenceProvider.Meta):
        """Django model options for the canonical test inference provider."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_provider"
        rebac_resource_type = "agents/inference_provider"
        rebac_id_attr = "sqid"


class InferenceModel(AbstractInferenceModel):
    """Concrete inference model catalogue row used by tests."""

    class Meta(AbstractInferenceModel.Meta):
        """Django model options for the canonical test inference model."""

        abstract = False
        app_label = "agents"
        db_table = "test_agents_inference_model"
        rebac_resource_type = "agents/inference_model"
        rebac_id_attr = "sqid"


AGENTS_TEST_MODELS = (Skill, InferenceProvider, InferenceModel)

SKILL_TREE = [
    {"path": "skills/calc/SKILL.md", "type": "blob", "oid": "a"},
    {"path": "skills/search/SKILL.md", "type": "blob", "oid": "b"},
    {"path": "skills/calc/README.md", "type": "blob", "oid": "c"},
]
SKILL_BLOBS = {
    "skills/calc/SKILL.md": "---\nname: Calculator\ndescription: arithmetic\n---\nbody",
    "skills/search/SKILL.md": "---\nname: Web Search\ndescription: search the web\n---\nbody",
}


@pytest.fixture()
def agents_tables(transactional_db: Any) -> Iterator[None]:
    """Create the iam/integrate/VCS/agents test tables and sync the REBAC schema."""

    del transactional_db
    created = _create_missing_tables(
        IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + VCS_TEST_MODELS + AGENTS_TEST_MODELS
    )
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


# --- parse_skill_meta (pure) --------------------------------------------------


def test_parse_skill_meta_reads_frontmatter() -> None:
    """The parser lifts name/description and keeps the rest as metadata."""

    descriptor = parse_skill_meta(b"---\nname: Calculator\ndescription: arithmetic\nversion: 2\n---\nbody")
    assert descriptor["name"] == "Calculator"
    assert descriptor["description"] == "arithmetic"
    assert descriptor["metadata"] == {"version": 2}


def test_parse_skill_meta_coerces_typed_values_json_safe() -> None:
    """An unquoted date becomes a ``date`` YAML-side; the parser keeps metadata JSON-safe."""

    descriptor = parse_skill_meta(b"---\nname: Dated\nreleased: 2024-01-15\n---\n")
    assert descriptor["metadata"] == {"released": "2024-01-15"}
    json.dumps(descriptor["metadata"])  # must not raise


def test_parse_skill_meta_tolerates_missing_or_malformed_frontmatter() -> None:
    """No frontmatter (or an unterminated block) yields an empty descriptor, not an error."""

    assert parse_skill_meta(b"# just a heading")["name"] == ""
    assert parse_skill_meta(b"---\nnot: [valid")["metadata"] == {}


# --- skill discovery ----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_skill_source_refresh_materializes_and_prunes(agents_tables: None) -> None:
    """A skill source refresh walks the tree for ``SKILL.md`` and upserts/prunes rows."""

    del agents_tables
    vcs = _vcs_integration("skills", config={"stub_repos": REPOS, "stub_tree": SKILL_TREE, "stub_blobs": SKILL_BLOBS})
    vcs.discover_repositories()
    with system_context(reason="test"):
        repository = Repository.objects.get(name="acme/widgets")
        source = Source.objects.create(repository=repository, kind="skill", path="skills")

    # README.md is ignored — only the two SKILL.md markers materialize.
    assert source.refresh() == 2
    with system_context(reason="test read"):
        skills = [(skill.name, skill.path) for skill in Skill.objects.filter(source=source).order_by("name")]
    assert skills == [("Calculator", "skills/calc"), ("Web Search", "skills/search")]

    # Drop the search skill from the tree → the next refresh prunes its row. Reload
    # the source so it reads the updated config fresh (the action path loads it anew),
    # rather than the related chain cached on this Python object.
    with system_context(reason="test"):
        vcs.integration.config = {"stub_repos": REPOS, "stub_tree": SKILL_TREE[:1], "stub_blobs": SKILL_BLOBS}
        vcs.integration.save(update_fields=["config", "updated_at"])
        source = Source.objects.get(pk=source.pk)
    assert source.refresh() == 1
    with system_context(reason="test read"):
        assert [skill.name for skill in Skill.objects.filter(source=source)] == ["Calculator"]


# --- inference catalogue sync -------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_inference_provider_refresh_upserts_models(agents_tables: None) -> None:
    """Refreshing a provider upserts one ``InferenceModel`` per advertised spec."""

    del agents_tables
    integration = make_integration("anthropic")
    with system_context(reason="test"):
        provider = InferenceProvider.objects.create(
            integration=integration,
            name="Anthropic",
            backend_class="stub",
            config={
                "stub_models": [
                    {
                        "handle": "claude-opus-4-8",
                        "display_name": "Claude Opus 4.8",
                        "model_use": "chat",
                        "context_window": 200000,
                    },
                    {"handle": "claude-haiku-4-5", "model_use": "chat"},
                ]
            },
        )

    assert provider.refresh_models() == 2
    with system_context(reason="test read"):
        models = {model.name: model for model in InferenceModel.objects.filter(provider=provider)}
    assert set(models) == {"claude-opus-4-8", "claude-haiku-4-5"}
    assert models["claude-opus-4-8"].display_name == "Claude Opus 4.8"
    assert models["claude-opus-4-8"].context_window == 200000
    # The spec omits display_name → it defaults to the wire handle.
    assert models["claude-haiku-4-5"].display_name == "claude-haiku-4-5"


@pytest.mark.django_db(transaction=True)
def test_manual_backend_advertises_no_models(agents_tables: None) -> None:
    """The built-in ``manual`` backend lists nothing — its catalogue is hand-curated."""

    del agents_tables
    integration = make_integration("manual-vendor")
    with system_context(reason="test"):
        provider = InferenceProvider.objects.create(integration=integration, name="Manual", backend_class="manual")
    assert provider.refresh_models() == 0
    with system_context(reason="test read"):
        assert InferenceModel.objects.filter(provider=provider).count() == 0


@pytest.mark.django_db(transaction=True)
def test_inference_provider_service_environment_reads_integration_credential_env(
    agents_tables: None,
) -> None:
    """Provider service env exposes only the integration-declared credential token."""

    del agents_tables
    integration = make_integration("anthropic-env")
    with system_context(reason="test service env setup"):
        oauth_client = OAuthClient.objects.create(
            slug="anthropic-oauth",
            display_name="Anthropic OAuth",
            client_id="public-client",
        )
        credential = Credential.objects.upsert_for_user(
            integration.owner,
            oauth_client,
            CredentialKind.OAUTH,
            {"access_token": "oauth-token"},
        )
        integration.credential = credential
        integration.config = {"credential_env": "ANTHROPIC_OAUTH_TOKEN"}
        integration.save(update_fields=["credential", "config", "updated_at"])
        provider = InferenceProvider.objects.create(
            integration=integration,
            name="Anthropic",
            backend_class="manual",
        )

    assert provider.service_environment() == {"ANTHROPIC_OAUTH_TOKEN": "oauth-token"}
    agent_like = SimpleNamespace(model=SimpleNamespace(provider=provider))
    assert AbstractAgent.service_environment(agent_like) == {"ANTHROPIC_OAUTH_TOKEN": "oauth-token"}

    with system_context(reason="test service env disabled"):
        integration.config = {}
        integration.save(update_fields=["config", "updated_at"])
        provider.refresh_from_db()

    assert provider.service_environment() == {}
