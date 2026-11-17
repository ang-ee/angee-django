"""Tests for MCP bearer identity resolution."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import override_settings
from rebac import system_context, to_object_ref
from rebac.backends import backend

from angee.agents.grants import tool_grant_ref
from angee.agents.mcp_verifier import resolve_actor
from angee.agents.models import MCPPlacement
from angee.agents.testing.models import Agent, MCPServer, MCPTool
from angee.integrate.credentials import CredentialKind
from angee.mcp.verifier import _verifier
from tests import test_integrate_vcs  # noqa: F401 -- register the concrete relation graph
from tests.conftest import Credential
from tests.test_integrate_jwt_bearer import neutral_key_file

User = get_user_model()


def test_mcp_verifier_resolves_optional_hook() -> None:
    """The base MCP transport declines when no verifier is configured."""

    with override_settings(ANGEE_MCP_ACTOR_VERIFIER=""):
        assert _verifier() is None
    with override_settings(ANGEE_MCP_ACTOR_VERIFIER="angee.agents.mcp_verifier.resolve_actor"):
        assert _verifier() is resolve_actor
    with (
        override_settings(ANGEE_MCP_ACTOR_VERIFIER="builtins.Ellipsis"),
        pytest.raises(ImproperlyConfigured, match="not callable"),
    ):
        _verifier()


def _static_credential(owner: User, *, name: str, token: str) -> Any:
    """Create a local static-token credential holding ``token`` for MCP verifier tests."""

    return Credential.objects.create_local_credential(
        owner, kind=str(CredentialKind.STATIC_TOKEN), name=name, material={"api_key": token}
    )


def _provisioned_agent(owner: User, *, name: str, workspace: str = "ws", service: str = "svc", **kwargs: Any) -> Any:
    """Create a READY/RUNNING agent, mirroring the operator's provision transitions."""

    agent = Agent.objects.create(name=name, owner=owner, **kwargs)
    agent.mark_provisioning()
    agent.mark_provisioned(workspace=workspace, service=service)
    return agent


def test_mcp_bearer_resolves_to_single_agent_principal(db: None) -> None:
    """An agent presenting its own derived bearer for an internal server runs as that agent."""

    owner = User.objects.create_user(username="mcp-agent-owner", email="mcp-agent@example.com")
    with system_context(reason="test.mcp.actor.single_agent"):
        credential = _static_credential(owner, name="mcp-bearer", token="tok-agent")
        server = MCPServer.objects.create(
            name="notes", url="http://x/mcp/notes/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="MCP Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    assert bearer.startswith(f"{agent.sqid}.")
    assert resolve_actor(bearer) == agent.principal_subject()


def test_mcp_bearer_declines_agent_without_service_user(db: None) -> None:
    """Legacy runnable rows without a service principal fail closed."""

    owner = User.objects.create_user(username="mcp-missing-user-owner")
    with system_context(reason="test.mcp.actor.missing_service_user"):
        credential = _static_credential(owner, name="mcp-missing-user", token="tok-missing-user")
        server = MCPServer.objects.create(
            name="missing-user",
            url="http://x/mcp/missing-user/",
            credential=credential,
            placement=MCPPlacement.INTERNAL,
        )
        agent = _provisioned_agent(owner, name="Missing User Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)
        Agent._base_manager.filter(pk=agent.pk).update(user=None)

    assert resolve_actor(bearer) is None


def test_mcp_bearer_declines_inactive_service_user(db: None) -> None:
    """Deactivation revokes a valid bearer without changing the agent or credential."""

    owner = User.objects.create_user(username="mcp-inactive-owner")
    with system_context(reason="test.mcp.actor.inactive_service_user"):
        credential = _static_credential(owner, name="inactive-user", token="tok-inactive")
        server = MCPServer.objects.create(
            name="inactive-user", url="http://x/mcp/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Inactive User Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)
    assert resolve_actor(bearer) == agent.principal_subject()
    with system_context(reason="test.mcp.actor.deactivate"):
        User._base_manager.filter(pk=agent.user_id).update(is_active=False)
    assert resolve_actor(bearer) is None


def test_mcp_bearer_resolves_for_ready_in_process_agent_without_operator_names(
    db: None,
) -> None:
    """A READY/RUNNING in-process agent authenticates despite rendering no workspace or service."""

    owner = User.objects.create_user(username="mcp-process-owner", email="mcp-process@example.com")
    with system_context(reason="test.mcp.actor.in_process"):
        credential = _static_credential(owner, name="mcp-process-bearer", token="tok-process")
        server = MCPServer.objects.create(
            name="angee", url="http://x/mcp/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Process Agent", workspace="", service="", runtime_class="pydantic")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    assert agent.workspace == "" and agent.service == ""
    assert resolve_actor(bearer) == agent.principal_subject()


def test_mcp_bearer_distinguishes_agents_sharing_one_internal_server(db: None) -> None:
    """Two agents on one internal server each resolve to their own subject; a spoof is declined."""

    owner = User.objects.create_user(username="mcp-shared-owner", email="mcp-shared@example.com")
    with system_context(reason="test.mcp.actor.per_agent"):
        credential = _static_credential(owner, name="shared-mcp-bearer", token="tok-shared")
        server = MCPServer.objects.create(
            name="shared", url="http://x/mcp/shared/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        first = _provisioned_agent(owner, name="First MCP Agent", workspace="ws-first", service="svc-first")
        second = _provisioned_agent(owner, name="Second MCP Agent", workspace="ws-second", service="svc-second")
        first.mcp_servers.add(server)
        second.mcp_servers.add(server)
        first_bearer = server.bearer_for(first)
        second_bearer = server.bearer_for(second)

    # Both share one server credential yet each derived bearer resolves to its own subject.
    assert first_bearer != second_bearer
    assert resolve_actor(first_bearer) == first.principal_subject()
    assert resolve_actor(second_bearer) == second.principal_subject()
    # A spoof — the second agent's sqid carrying the first agent's digest — is declined.
    _, _, first_digest = first_bearer.partition(".")
    assert resolve_actor(f"{second.sqid}.{first_digest}") is None


def test_mcp_bearer_raw_credential_secret_does_not_authenticate(db: None) -> None:
    """The raw server credential secret is no longer a valid bearer; the derived one is required."""

    owner = User.objects.create_user(username="mcp-raw-owner", email="mcp-raw@example.com")
    with system_context(reason="test.mcp.actor.raw_secret"):
        credential = _static_credential(owner, name="raw-mcp-bearer", token="tok-raw")
        server = MCPServer.objects.create(
            name="raw", url="http://x/mcp/raw/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Raw MCP Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    assert resolve_actor("tok-raw") is None
    assert resolve_actor(bearer) == agent.principal_subject()


def test_mcp_bearer_with_valid_agent_and_non_ascii_digest_is_declined(db: None) -> None:
    """Malformed Unicode digest input declines instead of reaching compare_digest."""

    owner = User.objects.create_user(username="mcp-unicode-owner", email="mcp-unicode@example.com")
    with system_context(reason="test.mcp.actor.unicode_digest"):
        credential = _static_credential(owner, name="unicode-mcp-bearer", token="tok-unicode")
        server = MCPServer.objects.create(
            name="unicode",
            url="http://x/mcp/unicode/",
            credential=credential,
            placement=MCPPlacement.INTERNAL,
        )
        agent = _provisioned_agent(owner, name="Unicode Digest Agent")
        agent.mcp_servers.add(server)

    assert resolve_actor(f"{agent.sqid}.{'é' * 64}") is None


@pytest.mark.parametrize("bearer", ["", "no-dot", ".", "agt_only.", ".digest-only"])
def test_mcp_bearer_malformed_is_declined(db: None, bearer: str) -> None:
    """A bearer that is empty or missing a non-empty sqid/digest half resolves to no actor."""

    assert resolve_actor(bearer) is None


def test_mcp_bearer_for_external_server_does_not_authenticate(db: None) -> None:
    """An external server verifies its own raw token, so its bearer never authenticates here."""

    owner = User.objects.create_user(username="mcp-external-owner", email="mcp-external@example.com")
    with system_context(reason="test.mcp.actor.external"):
        credential = _static_credential(owner, name="external-mcp-bearer", token="tok-external")
        server = MCPServer.objects.create(
            name="external", url="http://x/mcp/external/", credential=credential, placement=MCPPlacement.EXTERNAL
        )
        agent = _provisioned_agent(owner, name="External MCP Agent")
        agent.mcp_servers.add(server)
        authorization = server.authorization_for(agent)
        with pytest.raises(ValidationError, match="Only an internal MCP server"):
            server.bearer_for(agent)

    # An external server receives the credential's own authorization, whose raw token the
    # platform verifier declines: it only trusts the per-agent digest of an internal server.
    assert authorization == "Bearer tok-external"
    assert resolve_actor("tok-external") is None


@pytest.mark.parametrize("kind", [CredentialKind.STATIC_TOKEN, CredentialKind.JWT_BEARER])
def test_a_credential_with_no_secret_mints_and_verifies_no_bearer(db: None, kind: str) -> None:
    """An empty HMAC key would let anyone forge ``<sqid>.<HMAC("", sqid)>``; it verifies nothing."""

    owner = User.objects.create_user(username=f"mcp-forgery-{kind}", email=f"mcp-forgery-{kind}@example.com")
    material = {"api_key": ""} if kind == CredentialKind.STATIC_TOKEN else {"key_file": neutral_key_file()}
    with system_context(reason="test.mcp.actor.forgery"):
        credential = Credential.objects.create_local_credential(owner, kind=kind, name="keyless", material=material)
        # Created past ``clean``, as a row written before the kind check would be.
        server = MCPServer.objects.create(
            name="keyless", url="http://x/mcp/keyless/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Forgery target")
        agent.mcp_servers.add(server)
        with pytest.raises(ValidationError):
            server.bearer_for(agent)

    forged = f"{agent.sqid}.{hmac.new(b'', agent.sqid.encode(), hashlib.sha256).hexdigest()}"
    assert resolve_actor(forged) is None


@pytest.mark.parametrize(
    ("kind", "material", "accepted"),
    [
        pytest.param(CredentialKind.STATIC_TOKEN, {"api_key": "tok"}, True, id="static-token"),
        pytest.param(CredentialKind.BASIC_AUTH, {"username": "u", "password": "p"}, True, id="basic-auth"),
        pytest.param(CredentialKind.SSH_KEY, {"private_key": "key"}, False, id="ssh-key"),
        pytest.param(CredentialKind.APP_KEYS, {"app_id": "id", "app_secret": "secret"}, False, id="app-keys"),
        pytest.param(CredentialKind.JWT_BEARER, {"key_file": neutral_key_file()}, False, id="jwt-bearer"),
    ],
)
def test_an_mcp_server_accepts_only_credentials_that_send_scope_free_authorization(
    db: None, kind: str, material: dict[str, str], accepted: bool,
) -> None:
    """``MCPServer.clean``, which every console write runs, refuses kinds with no MCP authorization."""

    owner = User.objects.create_user(username=f"mcp-kind-{kind}", email=f"mcp-kind-{kind}@example.com")
    with system_context(reason="test.mcp.server.kind"):
        credential = Credential.objects.create_local_credential(owner, kind=kind, name=kind, material=material)
        server = MCPServer(name=f"kind-{kind}", url="https://mcp.example.test/", credential=credential)
        if accepted:
            server.full_clean()
        else:
            with pytest.raises(ValidationError) as refused:
                server.full_clean()
            assert set(refused.value.message_dict) == {"credential"}


INTERNAL_KEY = "k" * 40
"""An internal key comfortably above ``MCPServer.internal_key_min_length``."""


def test_revoking_the_internal_key_cuts_every_agent_bearer_off(db: None) -> None:
    """A bearer verifies only while its server's key credential is active."""

    owner = User.objects.create_user(username="mcp-revoked-owner", email="mcp-revoked@example.com")
    with system_context(reason="test.mcp.actor.revoked"):
        credential = _static_credential(owner, name="revoked-key", token=INTERNAL_KEY)
        server = MCPServer.objects.create(
            name="revoked", url="http://x/mcp/revoked/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Revoked key agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)
    assert resolve_actor(bearer) == agent.principal_subject()

    with system_context(reason="test.mcp.actor.revoke"):
        credential.status = "revoked"
        credential.save(update_fields=["status", "updated_at"])

    assert resolve_actor(bearer) is None
    with system_context(reason="test.mcp.actor.revoked.mint"), pytest.raises(ValidationError, match="not active"):
        MCPServer.objects.get(pk=server.pk).bearer_for(agent)


@pytest.mark.parametrize(
    ("kind", "material", "placement", "refusal"),
    [
        pytest.param(CredentialKind.STATIC_TOKEN, {"api_key": INTERNAL_KEY}, "internal", None, id="internal-static"),
        pytest.param(CredentialKind.STATIC_TOKEN, {"api_key": f"  {INTERNAL_KEY}  "}, "internal", None,
                     id="internal-key-is-stripped"),
        pytest.param(CredentialKind.STATIC_TOKEN, {"api_key": "k" * 31}, "internal", "at least 32", id="weak-key"),
        pytest.param(CredentialKind.STATIC_TOKEN, {"api_key": f"  {'k' * 31}  "}, "internal", "at least 32",
                     id="padding-does-not-count"),
        pytest.param(CredentialKind.BASIC_AUTH, {"username": "u", "password": INTERNAL_KEY}, "internal",
                     "static token", id="internal-basic-auth"),
        pytest.param(CredentialKind.BASIC_AUTH, {"username": "u", "password": "p"}, "external", None,
                     id="external-basic-auth"),
    ],
)
def test_an_internal_server_keys_bearers_only_with_a_strong_static_token(
    db: None, kind: str, material: dict[str, str], placement: str, refusal: str | None,
) -> None:
    """Rotating or provider-known credentials never key bearers; a weak key is refused at save."""

    owner = User.objects.create_user(username="mcp-key-owner", email="mcp-key@example.com")
    with system_context(reason="test.mcp.server.internal_kind"):
        credential = Credential.objects.create_local_credential(owner, kind=kind, name="key", material=material)
        server = MCPServer(name="keyed", url="https://mcp.example.test/", credential=credential, placement=placement)
        if refusal is None:
            server.full_clean()
        else:
            with pytest.raises(ValidationError, match=refusal):
                server.full_clean()


@pytest.mark.parametrize("other", ["internal-server", "external-server", "agent-inference"])
def test_an_internal_key_belongs_to_its_server_alone(db: None, other: str) -> None:
    """A key another record references is refused for an internal server, and vice versa."""

    owner = User.objects.create_user(username=f"mcp-shared-{other}", email=f"mcp-shared-{other}@example.com")
    with system_context(reason="test.mcp.server.shared"):
        key = _static_credential(owner, name="shared-key", token=INTERNAL_KEY)
        if other == "agent-inference":
            Agent.objects.create(name="Inference user", owner=owner, inference_credential=key)
        else:
            MCPServer.objects.create(
                name="holder", url="https://holder.example.test/", credential=key,
                placement=MCPPlacement.INTERNAL if other == "internal-server" else MCPPlacement.EXTERNAL,
            )
        internal = MCPServer(name="internal", url="https://mcp.example.test/", credential=key, placement="internal")
        with pytest.raises(ValidationError, match="must be its own"):
            internal.full_clean()
        if other == "internal-server":
            external = MCPServer(name="leak", url="https://elsewhere.example.test/", credential=key)
            with pytest.raises(ValidationError, match="another MCP server's internal key"):
                external.full_clean()


def test_internal_servers_never_accept_each_others_bearers(db: None) -> None:
    """The digest binds the server's sqid, so one key on two servers still mints distinct bearers."""

    owner = User.objects.create_user(username="mcp-bound-owner", email="mcp-bound@example.com")
    with system_context(reason="test.mcp.actor.bound"):
        key = _static_credential(owner, name="bound-key", token=INTERNAL_KEY)
        # Created past ``clean``, which would refuse the shared key.
        first, second = (
            MCPServer.objects.create(
                name=name, url=f"http://x/mcp/{name}/", credential=key, placement=MCPPlacement.INTERNAL
            )
            for name in ("first", "second")
        )
        agent = _provisioned_agent(owner, name="Bound agent")
        agent.mcp_servers.add(second)
        foreign, own = first.bearer_for(agent), second.bearer_for(agent)

    assert foreign != own
    assert resolve_actor(foreign) is None
    assert resolve_actor(own) == agent.principal_subject()


def test_the_demo_internal_server_key_is_generated_at_load_and_kept_on_reload(db: None) -> None:
    """No key is committed: the load generates one, and a reload keeps it."""

    resource_model = apps.get_model("resources", "Resource")
    addons = (apps.get_app_config("iam"), apps.get_app_config("agents"))
    resource_model.objects.load_addons(addons, tiers=[resource_model.Tier.DEMO], allow_non_dev=True)
    with system_context(reason="test.mcp.demo_key"):
        server = MCPServer.objects.select_related("credential").get(name="angee-demo")
        key = server.credential.secret_value()
        server.full_clean()

    assert server.credential.kind == CredentialKind.STATIC_TOKEN
    assert len(key) >= MCPServer.internal_key_min_length
    assert key != "demo-mcp-bearer-token"

    resource_model.objects.load_addons(addons, tiers=[resource_model.Tier.DEMO], allow_non_dev=True)
    with system_context(reason="test.mcp.demo_key.reload"):
        assert MCPServer.objects.select_related("credential").get(name="angee-demo").credential.secret_value() == key


def test_mcp_bearer_from_server_the_agent_lacks_is_declined(db: None) -> None:
    """A valid agent presenting a bearer for an internal server it is not attached to is declined."""

    owner = User.objects.create_user(username="mcp-unattached-owner", email="mcp-unattached@example.com")
    with system_context(reason="test.mcp.actor.unattached_server"):
        held_credential = _static_credential(owner, name="held-mcp-bearer", token="tok-held")
        other_credential = _static_credential(owner, name="other-mcp-bearer", token="tok-other")
        held = MCPServer.objects.create(
            name="held", url="http://x/mcp/held/", credential=held_credential, placement=MCPPlacement.INTERNAL
        )
        other = MCPServer.objects.create(
            name="other", url="http://x/mcp/other/", credential=other_credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Unattached MCP Agent")
        agent.mcp_servers.add(held)
        held_bearer = held.bearer_for(agent)
        other_bearer = other.bearer_for(agent)

    assert resolve_actor(held_bearer) == agent.principal_subject()
    assert resolve_actor(other_bearer) is None


def test_mcp_bearer_for_agent_with_no_attached_servers_is_declined(db: None) -> None:
    """A provisioned agent attached to no internal server resolves to no actor."""

    owner = User.objects.create_user(username="mcp-zero-owner", email="mcp-zero@example.com")
    with system_context(reason="test.mcp.actor.no_servers"):
        credential = _static_credential(owner, name="zero-agent-mcp-bearer", token="tok-zero")
        server = MCPServer.objects.create(
            name="zero", url="http://x/mcp/zero/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Detached MCP Agent")
        bearer = server.bearer_for(agent)

    assert resolve_actor(bearer) is None


def test_mcp_bearer_for_ready_agent_without_service_user_is_declined(db: None) -> None:
    """A valid bearer cannot act after its ready agent loses the service principal."""

    owner = User.objects.create_user(username="mcp-no-user-owner", email="mcp-no-user@example.com")
    with system_context(reason="test.mcp.actor.no_service_user"):
        credential = _static_credential(owner, name="no-user-mcp-bearer", token="tok-no-user")
        server = MCPServer.objects.create(
            name="no-user", url="http://x/mcp/no-user/", credential=credential, placement=MCPPlacement.INTERNAL
        )
        agent = _provisioned_agent(owner, name="Missing Principal Agent")
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)
        Agent._base_manager.filter(pk=agent.pk).update(user=None)

    assert resolve_actor(bearer) is None


@pytest.mark.parametrize(
    ("agent_kwargs", "mark_provisioned"),
    [
        ({"is_template": True}, True),
        ({}, False),
    ],
)
def test_mcp_bearer_ignores_template_and_unprovisioned_agents(
    db: None,
    agent_kwargs: dict[str, Any],
    mark_provisioned: bool,
) -> None:
    """Only concrete provisioned agents can act through an MCP bearer."""

    owner = User.objects.create_user(
        username=f"mcp-filter-owner-{mark_provisioned}",
        email=f"filter-{mark_provisioned}@example.com",
    )
    with system_context(reason="test.mcp.actor.filtered"):
        credential = _static_credential(
            owner, name=f"filtered-mcp-bearer-{mark_provisioned}", token=f"tok-filter-{mark_provisioned}"
        )
        server = MCPServer.objects.create(
            name=f"filtered-{mark_provisioned}",
            url="http://x/mcp/filter/",
            credential=credential,
            placement=MCPPlacement.INTERNAL,
        )
        agent = Agent.objects.create(name=f"Filtered {mark_provisioned}", owner=owner, **agent_kwargs)
        if mark_provisioned:
            agent.mark_provisioning()
            agent.mark_provisioned(
                workspace=f"ws-filtered-{mark_provisioned}",
                service=f"svc-filtered-{mark_provisioned}",
            )
        agent.mcp_servers.add(server)
        bearer = server.bearer_for(agent)

    assert resolve_actor(bearer) is None


def test_agent_mcp_m2m_reconciles_server_read_and_tool_use(db: None) -> None:
    """Server selection gates catalogue reads; tool selection gates invocation."""

    owner = User.objects.create_user(username="mcp-rebac-owner", email="mcp-rebac@example.com")
    with system_context(reason="test.mcp.rebac.seed"):
        server = MCPServer.objects.create(name="rebac-server", url="http://x/mcp/rebac/")
        tool = MCPTool.objects.create(server=server, name="rebac-tool")
        agent = Agent.objects.create(name="REBAC Agent", owner=owner)
        agent.mark_provisioning()
        agent.mark_provisioned(workspace="ws-rebac", service="svc-rebac")
        subject = agent.principal_subject()
        server_ref = to_object_ref(server)
        tool_ref = to_object_ref(tool)
        grant_ref = tool_grant_ref(str(server.sqid), tool.name)

        assert not backend().check_access(subject=subject, action="read", resource=server_ref).allowed
        agent.mcp_servers.add(server)
        assert backend().check_access(subject=subject, action="read", resource=server_ref).allowed
        agent.mcp_servers.remove(server)
        assert not backend().check_access(subject=subject, action="read", resource=server_ref).allowed

        assert not backend().check_access(subject=subject, action="read", resource=tool_ref).allowed
        assert not backend().check_access(subject=subject, action="use", resource=grant_ref).allowed
        agent.mcp_tools.add(tool)
        assert not backend().check_access(subject=subject, action="read", resource=tool_ref).allowed
        assert backend().check_access(subject=subject, action="use", resource=grant_ref).allowed
        agent.mcp_tools.remove(tool)
        assert not backend().check_access(subject=subject, action="read", resource=tool_ref).allowed
        assert not backend().check_access(subject=subject, action="use", resource=grant_ref).allowed
