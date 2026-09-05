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

Workflow one-shot configuration still accepts its existing prompt/system fields,
OpenAI or Anthropic function declarations, and vendor body options. Owned model,
message, token-limit and tool fields cannot be overwritten through `options`.
Common native settings pass directly; other supported vendor body fields are
encoded once into `extra_body`. Invalid/unsupported options fail the activity.

The persisted request summary keeps its vocabulary. New response summaries use
`format_version: 2`, native serialized message parts and native usage. Existing saved
journals remain stored in their original shape; this change adds no journal replay
adapter. The bounded journal limit remains 4096 bytes.
Budgets use `input_tokens`, `output_tokens` and `tokens`; existing persisted
`prompt_tokens`, `completion_tokens` and `total_tokens` budget axes remain
charged and enforced without double-counting the canonical total.
