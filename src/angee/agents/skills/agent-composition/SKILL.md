---
name: agent-composition
description: "Composes an agent in an Angee addon: the Agent row as a resource, its service-user subject (reach = grants), runtime_class and AgentRuntime, InferenceProvider backend_class and vendor backends, MCP servers, tool grants and Skill sources, provisioning verbs, ACP sessions and FakeRunner tests."
---

# Agent Composition

The reference example is `examples/src/example/notes/resources/demo/095_agents.agent.yaml`; `angee.agents` owns the
catalogue, the subject model and the seams; runtime and vendor addons own their implementations. Tool authoring is the
[mcp-tools](../../../mcp/skills/mcp-tools/SKILL.md) guide.

## Use when

- "Add / seed an agent for `<purpose>`" or "give the `<name>` agent access to `<records>`."
- "Add a runtime for `<program>`" or "add inference backend `<vendor>`."
- "Attach skills / MCP tools to an agent" or "let a workflow talk to an agent."
- "Provision / chat with the agent in tests."
- Not for tool bodies (`mcp-tools`), credentials (`angee.integrate`), or the stack's operator (`angee-runtime`).

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `<addon>/resources/<tier>/NNN_agents.agent.yaml` + `pyproject.toml [tool.angee.resources]` | the agent row and its catalogue dependencies | `examples/src/example/notes/resources/demo/095_agents.agent.yaml`, `notes/pyproject.toml:36` |
| `src/angee/agents/models/{agents,inference,skills,tools,sessions,choices}.py` | Agent policy, inference catalogue and request values, Skill parsing, MCP rows, session/turn transitions, shared enums; `models/__init__.py` preserves public imports | class docstrings |
| `src/angee/agents/runtimes.py`; `agents_runtime_pydantic/runtime.py` | `AgentRuntime` (:79), `NoRuntime` (:232), `OpenCodeRuntime` (:240), `ClaudeCodeRuntime` (:325), `operator_secret_ref` (:68); `PydanticAIRuntime` (:9) | docstrings |
| `src/angee/agents/backends.py`, `sdk_backends.py`; vendor `agents_integrate_*/backend.py` | `InferenceBackend` (:69), `ManualInferenceBackend` (:218); `SDKInferenceBackend` (:16); `AnthropicInferenceBackend` (`agents_integrate_anthropic/backend.py:68`) | module docstring |
| `src/angee/agents/autoconfig.py`; runtime/vendor `autoconfig.py` | `ANGEE_INFERENCE_BACKEND_CLASSES`, `ANGEE_AGENT_RUNTIME_CLASSES`, `ANGEE_INFERENCE_APPROVED_DEPLOYMENTS`, `ANGEE_MCP_ACTOR_VERIFIER`, `ANGEE_AGENT_CHAT_TOKEN_TTL` (:20-53); contributed keys (`agents_runtime_pydantic/autoconfig.py:5`, `agents_integrate_anthropic/autoconfig.py:5`) | comments |
| `src/angee/agents/managers.py`, `grants.py`, `permissions.zed`; `agents_runtime_pydantic/toolsets.py` | Native MCP catalogue managers; grant references and reader grants; protected runtime admission | owner docstrings |
| `src/angee/agents/models/skills.py`; historical `skills.py` alias | `Skill.parse_manifest`; `SkillManager.sync_from_source` using inherited Source completion | docstrings |
| `src/angee/agents/runners.py`, `sessions.py`, `tasks.py`, `acp_server.py`, `protocol.py` | `SessionRunner` (:32), `TurnOutcome` (:19); `run_next_turn` (:56); `run_session` (:9); ACP mappings; `ACP_PATH` (:3) | docstrings |
| `src/angee/agents/provisioning.py` | `provision_agent` :138, `adopt_agent` :147, `replace_agent` :156, `reprovision_agent` :165, `deprovision_agent` :174 | module docstring |
| `src/angee/agents/testing/` | concrete `Agent`, `InferenceProvider`, `InferenceModel`, `Skill`, … (`models.py`); `FakeRunner` (:24), `runner` fixture (:64) (`drivers.py`) | `tests/test_agents.py:1-11` |

## Reuse first

- An `Agent` row (resource or console) before any code: instructions, model, workspace template, runtime, servers, skills are data.
- `Agent.principal_subject()` + `actor_context` for anything done *as* the agent; `agent.owner` only manages the definition.
- `InferenceModel.infer` / `objects.usable(actor, role, uses=)` / `require_usable` for one-shot inference from domain code; never a vendor client.
- `angee.iam.models.ServiceUserMixin` supplies Agent’s native user relation and `sync_service_user` / `deactivate_service_user`; Agent calls its own methods on save, provisioning and delete.
- `integrate.Credential` and `integrate_vcs.Source`/`Template` for secrets, skill sources and workspace templates.
- `workflows_agents` steps `start_conversation`/`close_conversation` (`src/angee/workflows_agents/steps.py`) when a workflow needs a turn.
- Upstream: pydantic-ai (`Model`, toolsets, `UsageLimits`), `agent-client-protocol` SDK, FastMCP — [Stack](../../../../../docs/stack.md#backend) rows 66, 96, 98.

## Steps

1. **Decide what is data and what is code.** A new agent, its prompt, model, tools and skills are an `Agent` row (step 2).
   Code is needed only for a new runtime (step 4), a new inference backend (step 5) or new tools (`mcp-tools`).
   **Owner rule:** [Repository role](../../../../../AGENTS.md#repository-role); product agents live in the consumer addon's resources.

2. **Declare the agent row as a resource.** Nested `_meta`/`rows` form, because `Agent` has a `model` field:

   ```yaml
   _meta: {model: agents.Agent}
   rows:
     - _xref: agent_demo
       fields:
         name: Demo Agent
         owner: iam.user_admin
         instructions: |
           You are a helpful coding agent working in /workspace.
         model: agents_integrate_anthropic.model_claude_demo
         workspace_template: notes.tpl_agent_default
         runtime_class: claude_code
         mcp_servers: [agents.mcp_angee]
   ```

   `pyproject.toml` lists the file with `depends_on` on the vendor model file and `agents:resources/demo/020_agents.mcpserver.yaml`
   (`notes/pyproject.toml:36`); `AgentResource` syncs the built-in tool catalogue before tool selections resolve (`resources.py:11`).
   **Owner rule:** row mechanics belong to `angee-resources`; an agent installed without an owner waits for an administrator
   transfer (`models/agents.py` class docstring).

3. **Treat the service user as the subject.** `Agent.save` keeps a `kind=service` user in `agent.user`; `principal_subject()` is
   the actor for turns, tool calls and workflow conversations, and its reach is exactly the grants on that user (zed `actor`
   relation; `tool_grant.selected_agent`). To widen reach, grant the service user (`grant_record_access`, group membership,
   a role), never the owner. **Owner rule:** [REBAC](../../../../../docs/backend/guidelines.md#rebac), "One user identity for
   authorization and attribution"; [Glossary: Agent](../../../../../docs/glossary.md#backend).

4. **Select or add a runtime.** `runtime_class` is an `ImplClassField(AgentRuntime)` keyed by `ANGEE_AGENT_RUNTIME_CLASSES`
   (`none`, `claude_code`, `opencode` from agents; `pydantic` from `agents_runtime_pydantic`). A runtime owns the operator
   service template, credential-to-env mapping (`auth_env`), the model-handle convention and, for in-process runtimes, a
   `session_runner_class`:

   ```python
   class PydanticAIRuntime(AgentRuntime):
       key = "pydantic"
       label = "In-process (pydantic-ai)"
       service_template_name = ""
       session_runner_class = "angee.agents_runtime_pydantic.runner.PydanticAISessionRunner"
       supported_credential_kinds = frozenset({CredentialKind.STATIC_TOKEN, CredentialKind.OAUTH})
   ```

   A new runtime is its own addon contributing `"ANGEE_AGENT_RUNTIME_CLASSES.<key>"` in `autoconfig.py` and a `SessionRunner`
   implementing `run_turn` (`runners.py:32`). **Owner rule:** the registry field selects behaviour over one persisted shape
   ([Rules](../../../../../docs/backend/guidelines.md#rules), "Choosing how a row selects per-variant behaviour").

5. **Select or add inference.** An `InferenceProvider` (an `integrate.Integration` child) selects `backend_class` from
   `ANGEE_INFERENCE_BACKEND_CLASSES`; the backend lists `InferenceModel` rows; `agent.model` points at one and
   `inference_credential` overrides the provider's credential. A vendor addon subclasses `SDKInferenceBackend`, sets
   `api_key_env`, client paths and `transient_error_paths`, and contributes `"ANGEE_INFERENCE_BACKEND_CLASSES.<key>"`
   (`agents_integrate_anthropic/autoconfig.py:5`). **Owner rule:** `InferenceBackend` owns endpoint and transient-error
   classification, vendor addons SDK and transport policy ([Stack](../../../../../docs/stack.md#backend), pydantic-ai row).

6. **Attach tools and skills.** `mcp_servers`/`mcp_tools` select catalogue rows; the built-in `agents.mcp_angee` server is
   synced from the registered FastMCP tools (`grants.py:82`), and a tool is usable only through an `agents/tool_grant`
   tuple (`use`, re-checked per call by `ToolGrantAccess`). Skills are `Skill` rows discovered from an `integrate_vcs.Source`
   with `source_kind="skill"` (`SkillManager.sync_from_source`, SKILL.md frontmatter) and selected through `Agent.skills`;
   the operator mounts them. **Owner rule:** `angee.mcp` owns registration (`src/angee/mcp/server.py:92`), agents the
   catalogue and grants; tool bodies are authored with [mcp-tools](../../../mcp/skills/mcp-tools/SKILL.md).

7. **Provision through the verbs.** `provision_agent`, `adopt_agent`, `replace_agent`, `reprovision_agent`, `deprovision_agent`
   are the GraphQL actions; `Agent.provision_blocker()` says why one may not start; `lifecycle` moves through
   `StateTransitions`; secrets reach containers as `operator_secret_ref` placeholders, never values. **Owner rule:** the
   operator daemon owns instances, `provisioning.py` the orchestration (module docstring); stack operations are `angee-runtime`.

8. **Converse over ACP.** `AgentSession`/`AgentTurn` persist the chat; `run_session` (`tasks.py:9`) calls `run_next_turn`, which
   claims one turn outside any transaction and runs it under `actor_context(principal)` through the runtime's
   `SessionRunner.run_turn`, streaming ACP `session/update` payloads; the WebSocket route is `ACP_PATH` (`acp_server.py`).
   **Owner rule:** agents owns the seam, runtime addons the loop (`runners.py` docstring); the SDK owns protocol and schemas
   ([Stack](../../../../../docs/stack.md#backend), agent-client-protocol row).

9. **Test with the shared concretes.** `angee.agents.testing.models` supplies the tables; `FakeRunner` and the `runner` fixture
   (`testing/drivers.py:24,64`) stand in for a runtime; the `stub_inference` backend for a provider (`tests/test_agents.py`).
   Assert actions ran as the principal: `with actor_context(session.agent.principal_subject())` (`tests/test_agent_sessions.py:605`);
   ACP through `WebsocketCommunicator` (`tests/test_agents_acp.py`). **Owner rule:** [source-addon test models](../../../../../docs/checks.md#source-addon-test-models).

## Do not

- Acting as `agent.owner` or under `system_context` for the agent's work: `principal_subject()` ([REBAC](../../../../../docs/backend/guidelines.md#rebac)).
- A `kind` column, dotted path or per-agent branch to pick a runtime or backend: `runtime_class`/`backend_class` registries ([Rules](../../../../../docs/backend/guidelines.md#rules)).
- A vendor SDK import or product OAuth client in `angee.agents`: the vendor backend addon (`backends.py:1-6`, "Vendor-neutral").
- A secret value in a row, config file or browser payload: `operator_secret_ref` and the operator secret store (`runtimes.py:68`).
- Calling a tool body with the agent's bearer around the grant check: `ToolGrantAccess` per call (`toolsets.py:67`; `mcp_verifier.py` docstring).
- An agent-local FastMCP server or tool table: the addon `mcp_tools.py` registrar (`src/angee/mcp/server.py:92`).
- Running a turn inside `transaction.atomic` or retrying a crashed turn: `run_next_turn` refuses, no delivery reclaims (`sessions.py:1-6,60`).
- Creating agents with `Agent.objects.create` in seeds or commands: a resource row (`AgentResource`, `resources.py:11`).
- A caveated subject in the agents zed: `angee.E024` (`src/angee/iam/checks.py:37`); field-backed conditional relations.

## Verify

Done means: the resource loads an `Agent` with `lifecycle=draft` and a `kind=service` user; registry checks for
`runtime_class`/`backend_class` are clean (`angee.E001`-`angee.E005`); `provision_blocker()` is `None` once a usable
credential is set; a `FakeRunner` turn settles `completed` with every captured actor equal to `principal_subject()`.

- In a lane (jj workspace: no host, database or JS install): focused pytest (`tests/test_agents*.py`, `tests/test_agent_*.py`),
  ruff, mypy on touched files, `tests/test_layering.py` — [Python and composition contracts](../../../../../docs/checks.md#python-and-composition-contracts).
- Only on integrated `src`: provisioning against the running operator, the ACP socket through the stack, console pages and
  codegen — [composition and schema](../../../../../docs/checks.md#composition-and-schema); the agents GraphQL campaigns are
  among the heaviest CI files ([what CI actually runs](../../../../../docs/checks.md#what-ci-actually-runs)).
- Loop: [slice](../../../../../.agents/skills/slice/SKILL.md#workflow). False greens: `FakeRunner` and pydantic-ai's `TestModel`
  prove persistence and the loop, not vendor transport; a green lane never provisions.

## Pitfalls

- The agent YAML must use the nested `_meta`/`rows` form: the flat form's reserved `model` key collides with `Agent.model` (`095_agents.agent.yaml` header).
- Deleting an agent requires teardown and stopped turns on every collector path; deleting its owner only clears `owner` (`models/agents.py` class docstring).
- A configured `ANGEE_INFERENCE_APPROVED_DEPLOYMENTS` fails closed for a missing or non-matching role (`autoconfig.py:28`).
- A runtime without `session_runner_class` uses the operator transport, which requires an administrator to chat (`runtimes.py:115-125`).
- OAuth credentials are runtime-specific: a runtime lists its `supported_credential_kinds` and refuses the rest up front (`runtimes.py:96,275`).
- `inference_credential` overrides the provider chain only when set; provisioning is refused while the credential carries no secret (`095_agents.agent.yaml` header).

## Read next

- [REBAC](../../../../../docs/backend/guidelines.md#rebac), [Rules](../../../../../docs/backend/guidelines.md#rules), [Integration and worker contracts](../../../../../docs/backend/guidelines.md#integration-and-worker-contracts)
- [Glossary: Backend](../../../../../docs/glossary.md#backend) (Principal, Service account, Agent, Backend class), [Stack](../../../../../docs/stack.md#backend)
- [Workflows README](../../../workflows/README.md) for `workflows_agents` conversations
- Skills: `mcp-tools`, `angee-resources`, `angee-permissions`, `workflow-definitions`, `angee-runtime`, `slice`

## Sources

<!--
Docs: docs/glossary.md #backend (Backend class, Implementation, Agent entries); docs/backend/guidelines.md #rules (implementation-registry bullets), #direct-record-access; docs/stack.md #backend (pydantic-ai, anthropic, FastMCP rows); docs/checks.md #source-addon-test-models, #browser-verification; AGENTS.md.
Code: src/angee/agents/{pyproject.toml,autoconfig.py,apps.py,models/,managers.py,runtimes.py,backends.py,sdk_backends.py,skills.py,runners.py,sessions.py,tasks.py,protocol.py,acp_server.py,grants.py,mcp_verifier.py,provisioning.py,resources.py,context.py,permissions.zed,signals.py,testing/}; src/angee/agents_runtime_pydantic/{runtime.py,toolsets.py}; src/angee/agents_integrate_anthropic/backend.py; src/angee/workflows_agents/steps.py; src/angee/mcp/{server.py,graphql.py}; examples/src/example/notes/; tests/{test_agents.py,test_agents_runtime_pydantic.py,test_agents_acp.py,test_agent_sessions.py,test_agent_tooling.py}.
Doc gaps: src/angee/agents has no README; the Agent/runtime/backend contracts are documented only in docstrings and the stack rows. Tool authoring is owned by the mcp-tools guide (src/angee/mcp/skills/mcp-tools/SKILL.md), linked by path only.
Mismatches: none found between docstrings and the cited code.
-->
