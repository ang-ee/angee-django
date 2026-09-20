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
`model_settings=...`, `model_request_parameters=...` and `credential=...`.
`InferenceModel.chat` resolves its wire model name and takes native messages as
its first argument. It returns `pydantic_ai.messages.ModelResponse`.
`InferenceRequest`, `InferenceResponse` and `ChatAPI` no longer exist.

The direct call makes one logical model request; the SDK may retry transport
failures. Declare tools with native
`ModelRequestParameters(function_tools=[ToolDefinition(...)])`; returned tool
calls remain response parts. Only a session runtime runs a tool loop. For async
sessions, call `agent.inference_model()` from the synchronous Django boundary,
then `async with binding as model` inside the runner. Custom backends implement
`model(handle, *, credential=None)` and return a native model context. They do
not add a second request/response protocol. Client contexts close on normal
completion, provider errors and cancellation.

Workflow one-shot inference uses the `infer` step. Its exact contracts are
`InferInput`/`InferRequest` and `InferOutput` on
[`InferStepImpl`](../../addons/angee/workflows_agents/steps.py). Bind `model` to
an `InferenceModel` public id (sqid) through `workflow_input` or `step_output`,
and supply the required policy `role`; a workflow definition must not hard-code
the selected model. `request` accepts native Pydantic AI `messages`, `images`,
`output_schema`, and `settings` restricted to `ModelSettings` keys. The step-level `timeout` is separate and
cannot also appear in `request.settings`. Transport overrides such as
`extra_headers`, `extra_query`, and arbitrary `extra_body` keys are rejected by
the backend owner. `infer` always uses the credential owned by the selected
provider; it has no agent-credential override.

The step resolves the catalogue row and applies the configured role policy
before invoking the provider. An absent
`ANGEE_INFERENCE_APPROVED_DEPLOYMENTS` setting leaves the catalogue
unrestricted; once configured, missing roles and exact deployment-identity
mismatches are rejected. Provider success returns the serialized native
`ModelResponse` plus usage and routes `completed`. A terminal provider failure
returns `response: null`, normalized usage, and `{type, message}` error data on
`failed`; retryable provider failures use the workflow retry policy. Adapter
timestamps and provider ids in the response are retained evidence, not stable
hash or reuse keys.

[`normalize_inference_usage`](../../addons/angee/agents/models.py) is the one
usage projection for direct requests and agent sessions. It emits non-zero
`input_tokens`, `output_tokens`, `tokens`, `requests`, and `tool_calls` axes;
`requests` remains a workflow budget axis because the run budget owner admits
arbitrary numeric axes.
