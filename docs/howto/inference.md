# Native in-process inference

Angee's catalogue selects a Pydantic AI model through the vendor addon. The
backend resolves credentials synchronously, then returns an async context that
owns the official SDK client's lifetime. OpenAI-compatible backends, including
Ollama, specialize the OpenAI binding; Anthropic retains its OAuth headers and
required wire preamble. Vendor request/response serialization belongs to the
native model adapters.

For one request from synchronous Django code:

```python
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart

response = model.chat(
    [ModelRequest(parts=[SystemPromptPart("Be concise."), UserPromptPart("Hello")])],
    model_settings={"max_tokens": 128},
)
print(response.text)
print(response.usage.input_tokens, response.usage.output_tokens)
```

`InferenceProvider.chat` accepts keyword-only `model=...`, `messages=...`,
`model_settings=...`, `model_request_parameters=...`, and `credential=...`.
[`InferenceModel.chat`](../../addons/angee/agents/models.py) resolves its wire
model name and takes native messages as its first argument. It returns
`pydantic_ai.messages.ModelResponse`.

For structured output, call `model.require_usable(actor, role, uses=uses)` at the
entry of the authorized operation. Declare accepted model uses with
`InferenceModelUse` members, then call `model.infer(messages, output_schema=schema)`.
`InferenceResult` exposes the native `response`, normalized
`usage`, and decoded `output` object. JSON text, fenced JSON, and native output
tools share the agents decoder. Function-tool declarations return calls in the
native response and leave `output` unset. A malformed structured response raises
`InferenceOutputError`, retaining its `response` and `usage` for accounting.

The direct call makes one logical model request; the SDK may retry transport
failures. Declare tools with native
`ModelRequestParameters(function_tools=[ToolDefinition(...)])`; returned tool
calls remain response parts. Only a session runtime runs a tool loop. For async
sessions, call `agent.inference_model()` from the synchronous Django boundary,
then `async with binding as model` inside the runner. Custom backends implement
`model(handle, *, credential=None)` and return a native model context. They do
not add a second request/response protocol. Client contexts close on normal
completion, provider errors and cancellation.

[`InferenceModel.require_usable`](../../addons/angee/agents/models.py) requires
the actor's read access, approved deployment identity and a live model with an
accepted use. Roles name approval policies independently of modality; callers
declare their accepted `uses`. An absent
`ANGEE_INFERENCE_APPROVED_DEPLOYMENTS` setting leaves the catalogue
unrestricted by deployment policy; actor read access remains required. Once
configured, missing roles and exact deployment-identity mismatches are rejected.
The effective endpoint comes from `InferenceBackend.endpoint` for both SDK
clients and deployment approval. Provider and credential persistence follows the
[Database routing rule](../backend/guidelines.md#rules).

[`InferenceBackend.is_transient_error`](../../addons/angee/agents/backends.py)
classifies native HTTP 429/server errors and connection/timeout exceptions;
vendor adapters extend that policy with SDK exception types. Invalid local
timeout settings are terminal configuration errors. Adapter timestamps and
provider ids in the response are retained evidence, not stable hash or reuse keys.

[`normalize_inference_usage`](../../addons/angee/agents/models.py) is the one
usage projection for direct requests and agent sessions. It emits non-zero
`input_tokens`, `output_tokens`, `tokens`, `requests`, and `tool_calls` values.

The independent [`extraction`](../../addons/angee/extraction/README.md) domain
uses `InferenceModel.infer` for grounded recognition and mapping. Its
[`workflows_extraction`](../../addons/angee/workflows_extraction/README.md)
adapter owns the workflow graph and steps: `infer_evidence` reads a pinned
extraction revision, rechecks source access, and routes an unavailable model to
the `inference_failed` outcome with a reader-facing reason. Provider selection,
source retention and correction authority stay with extraction and agents.
