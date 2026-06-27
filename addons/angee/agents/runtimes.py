"""Agent runtimes — the programs an agent is rendered into (claude-code, opencode).

An :class:`Agent` selects one of these by ``runtime_class`` (an ``ImplClassField``
keyed against ``ANGEE_AGENT_RUNTIME_CLASSES``). The runtime owns the facts that
differ between agent programs and that the inference *provider* does not know:

* which operator service template renders it (``service_template_name``),
* how it consumes an inference credential as container env (:meth:`auth_env`) —
  the same Anthropic OAuth token feeds ``claude-code``'s ``CLAUDE_CODE_OAUTH_TOKEN``
  but ``opencode`` reads only ``ANTHROPIC_API_KEY``, so the env block is a
  ``(runtime × provider × credential-kind)`` fact, not a provider-only one,
* which credential kinds it can actually use (:attr:`supported_credential_kinds`),
  so the provision flow refuses an unworkable pairing up front instead of
  rendering a service that silently falls back,
* the secret *payload* synced to the operator (:meth:`auth_secret_value`) — most
  runtimes sync the raw token, but OpenCode's OAuth path syncs a base64 ``auth.json``,
* the model handle in its own convention (:meth:`model_handle`).

The inference backend stays the owner of vendor-native primitives (the credential
value and the vendor SDK's own ``api_key_env``); a runtime composes those.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings

from angee.base.impl import ImplBase
from angee.integrate.credentials import CredentialKind

if TYPE_CHECKING:
    from angee.agents.backends import InferenceBackend

# Anthropic requires this beta opt-in header to accept an OAuth (Personal Plans)
# token. Shared by the claude-code runtime's container env and the Anthropic SDK
# backend's own client (which imports it from here — the vendor addon depends on
# agents, not the reverse).
ANTHROPIC_OAUTH_BETA_HEADER = "oauth-2025-04-20"


def operator_secret_ref(secret_name: str) -> str:
    """Return the operator placeholder that resolves to a stored secret in the container.

    The single home of the ``${secret.<name>}`` operator-secret contract that both
    the inference auth env and the per-MCP-server bearer env reference; the operator
    substitutes the value at compose-render, so it never transits the browser.
    """

    return f"${{secret.{secret_name}}}"


class AgentRuntime(ImplBase):
    """Base agent runtime: the generic, API-key-only program (e.g. opencode).

    Reads the provider's own ``api_key_env`` and renders the catalogue model handle
    verbatim. Subclasses override for runtime-specific auth shapes (claude-code's
    OAuth env) or handle conventions.
    """

    category = "agent_runtime"
    icon = "robot"
    # The operator service template this runtime renders; "" means the runtime
    # renders no service (a workspace-only agent).
    service_template_name: ClassVar[str] = ""
    service_template_kind: ClassVar[str] = "service"
    # Credential kinds this runtime can consume for inference. The generic runtime
    # accepts only a static API key; OAuth support is runtime-specific. Typed as a bare
    # ClassVar because (without django-stubs) mypy reads a TextChoices member as its
    # (value, label) tuple — the set holds CredentialKind members, matched by equality.
    supported_credential_kinds: ClassVar = frozenset({CredentialKind.STATIC_TOKEN})

    @property
    def renders_service(self) -> bool:
        """Whether this runtime renders an operator service (vs workspace-only)."""

        return bool(self.service_template_name)

    def supports_credential(self, credential: Any) -> bool:
        """Whether this runtime can authenticate inference with ``credential``.

        Takes the whole credential (not just its kind) so a runtime can refuse a
        credential whose *shape* it can't use — e.g. an OAuth token with no refresh
        token — up front, not only an unsupported kind.
        """

        return credential.kind in self.supported_credential_kinds

    def model_handle(self, model: Any) -> str:
        """Return the selected model handle in this runtime's convention.

        The generic runtime renders the catalogue handle verbatim (e.g. opencode's
        ``anthropic/claude-…`` provider-qualified handle).
        """

        return str(getattr(model, "name", "") or "")

    def auth_env(self, *, backend: InferenceBackend, credential: Any, secret_name: str) -> dict[str, str]:
        """Return the container env block this runtime uses to authenticate inference.

        Secret-bearing vars carry the operator ``${secret.<name>}`` placeholder; the
        value is synced server-side. An unsupported credential kind fails here, at the
        runtime owner, before a service is rendered.
        """

        if not self.supports_credential(credential):
            raise ValueError(f"The {self.display_label()} runtime cannot use a {credential.kind} credential.")
        return self._api_key_env(backend, secret_name)

    def auth_secret_value(self, credential: Any) -> str:
        """Return the secret payload synced to the operator store for ``credential``.

        The value stored under the agent's inference secret name and that
        :meth:`auth_env`'s ``${secret.<name>}`` placeholder resolves to in the container.
        The generic runtime syncs the raw credential secret (API key or bearer token); a
        runtime needing a richer shape (OpenCode's OAuth ``auth.json``) overrides this.
        """

        return str(credential.secret_value() or "")

    @staticmethod
    def _api_key_env(backend: InferenceBackend, secret_name: str) -> dict[str, str]:
        """Return ``{vendor_api_key_env: placeholder}`` from the provider's own env names."""

        names = backend.api_key_env
        if not names:
            raise ValueError(f"{backend.label} inference does not declare an API-key env var.")
        placeholder = operator_secret_ref(secret_name)
        return {name: placeholder for name in names}


class NoRuntime(AgentRuntime):
    """Null-object runtime for a workspace-only agent — renders no service."""

    label = "None"
    service_template_name = ""


class OpenCodeRuntime(AgentRuntime):
    """OpenCode (ACP over a WebSocket): an API key, or Anthropic Personal-Plans OAuth.

    A static key renders the provider's own key env (``ANTHROPIC_API_KEY`` etc.). OpenCode
    has *no env path* for an OAuth token — it reads OAuth only from its native ``auth.json``
    store and needs a community Anthropic auth plugin (bundled in the opencode service image)
    to refresh and bear the token. We sync that store as a base64 blob in
    :attr:`oauth_env`; the image entrypoint decodes it into ``OPENCODE_AUTH_CONTENT``.
    base64 keeps the JSON (quotes, braces) opaque to YAML and the operator's secret
    substitution. The model handle is the catalogue's provider-qualified id (e.g.
    ``anthropic/claude-opus-4-8``).

    WARNING: using a Claude Pro/Max (Personal Plans) OAuth token in OpenCode is against
    Anthropic's terms and risks account suspension — OpenCode removed built-in support in
    1.3.0. Prefer a static API-key credential. OAuth stays disabled until the operator
    sets :attr:`oauth_enabled_setting` *and* builds the opencode image with the auth plugin
    (see ``OPENCODE_ANTHROPIC_AUTH_PLUGIN`` in the service Dockerfile); enabling it without
    the plugin would silently drop Anthropic from the model list.
    """

    label = "OpenCode"
    icon = "opencode"
    service_template_name = "opencode"
    # OpenCode OAuth via the community Anthropic plugin is Anthropic-only; this is the
    # provider id key under which the token is stored in OpenCode's auth.json.
    oauth_provider_id: ClassVar[str] = "anthropic"
    # Container env carrying the base64 auth.json; the image entrypoint decodes it into
    # OPENCODE_AUTH_CONTENT before launching opencode.
    oauth_env: ClassVar[str] = "ANGEE_OPENCODE_AUTH_B64"
    # OAuth needs a community Anthropic plugin in the opencode image. Allowing OAuth without
    # it would silently drop Anthropic from the model list (the original bug), so OAuth is
    # gated on an operator opt-in that pairs with building the image with the plugin.
    oauth_enabled_setting: ClassVar[str] = "ANGEE_OPENCODE_OAUTH_ENABLED"

    def supports_credential(self, credential: Any) -> bool:
        """Allow OAuth only when opted in (plugin bundled) and the token can be refreshed.

        OpenCode's plugin renews the access token from the stored refresh token, so an OAuth
        credential carrying no refresh token would authenticate only until the short-lived
        access token expires — refuse it up front rather than provision an agent that dies
        silently. Static keys defer to the base (which OpenCode always supports).
        """

        if credential.kind == CredentialKind.OAUTH:
            if not bool(getattr(settings, self.oauth_enabled_setting, False)):
                return False
            return bool(credential.reveal().get("refresh_token"))
        return super().supports_credential(credential)

    def auth_env(self, *, backend: InferenceBackend, credential: Any, secret_name: str) -> dict[str, str]:
        """Render the OAuth auth.json env (base64), or the provider's static key env."""

        if credential.kind == CredentialKind.OAUTH:
            if not self.supports_credential(credential):
                raise ValueError(f"The {self.display_label()} runtime cannot use a {credential.kind} credential.")
            return {self.oauth_env: operator_secret_ref(secret_name)}
        return super().auth_env(backend=backend, credential=credential, secret_name=secret_name)

    def auth_secret_value(self, credential: Any) -> str:
        """Sync OpenCode's base64 ``auth.json`` for OAuth, else the raw key (static)."""

        if credential.kind == CredentialKind.OAUTH:
            return self._oauth_auth_content(credential)
        return super().auth_secret_value(credential)

    def _oauth_auth_content(self, credential: Any) -> str:
        """Return base64 of OpenCode's ``auth.json`` for an Anthropic OAuth credential.

        The plugin refreshes the access token from the stored refresh token, so the
        refresh token (not just a short-lived access token) is what makes this durable.
        """

        material = credential.reveal()
        expires = getattr(credential, "expires_at", None)
        entry = {
            "type": "oauth",
            "refresh": str(material.get("refresh_token") or ""),
            "access": str(material.get("access_token") or ""),
            "expires": int(expires.timestamp() * 1000) if expires is not None else 0,
        }
        payload = json.dumps({self.oauth_provider_id: entry}, separators=(",", ":"))
        return base64.b64encode(payload.encode()).decode()


class ClaudeCodeRuntime(AgentRuntime):
    """Claude Code: Anthropic-native, accepts both a static key and an OAuth token.

    An OAuth credential renders Claude Code's token env plus the Anthropic beta
    header; a static key renders the vendor ``ANTHROPIC_API_KEY``. The handle is the
    provider's native model name (Claude Code talks to Anthropic directly).
    """

    label = "Claude Code"
    icon = "anthropic"
    service_template_name = "claude-code"
    supported_credential_kinds = frozenset({CredentialKind.STATIC_TOKEN, CredentialKind.OAUTH})

    def model_handle(self, model: Any) -> str:
        """Return the provider's native model name, falling back to the catalogue handle."""

        return str(getattr(model, "provider_model_name", "") or getattr(model, "name", "") or "")

    def auth_env(self, *, backend: InferenceBackend, credential: Any, secret_name: str) -> dict[str, str]:
        """Render Claude Code's OAuth token env (plus beta header), or the static key env."""

        if credential.kind == CredentialKind.OAUTH:
            placeholder = operator_secret_ref(secret_name)
            return {
                "ANTHROPIC_AUTH_TOKEN": placeholder,
                "CLAUDE_CODE_OAUTH_TOKEN": placeholder,
                "ANTHROPIC_CUSTOM_HEADERS": f"anthropic-beta: {ANTHROPIC_OAUTH_BETA_HEADER}",
            }
        return super().auth_env(backend=backend, credential=credential, secret_name=secret_name)
