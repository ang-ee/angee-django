import { describe, expect, test, vi } from "vitest";

import { buildPromptBlocks, selectSessionModel } from "./useAcpRuntime";

describe("selectSessionModel", () => {
  test("switches from ACP default to the agent model handle", async () => {
    const setSessionModel = vi.fn(async () => undefined);

    await selectSessionModel(
      { setSessionModel } as never,
      {
        sessionId: "session-1",
        models: {
          currentModelId: "default",
          availableModels: [
            { modelId: "default", name: "Default" },
            { modelId: "claude-opus-4-8", name: "claude-opus-4-8" },
          ],
        },
      },
      "claude-opus-4-8",
    );

    expect(setSessionModel).toHaveBeenCalledWith({
      sessionId: "session-1",
      modelId: "claude-opus-4-8",
    });
  });

  test("does not switch when the selected model is already current", async () => {
    const setSessionModel = vi.fn(async () => undefined);

    await selectSessionModel(
      { setSessionModel } as never,
      {
        sessionId: "session-1",
        models: {
          currentModelId: "claude-opus-4-8",
          availableModels: [{ modelId: "claude-opus-4-8", name: "claude-opus-4-8" }],
        },
      },
      "claude-opus-4-8",
    );

    expect(setSessionModel).not.toHaveBeenCalled();
  });

  test("defers to the agent when it advertises no standard model state", async () => {
    // opencode owns its model via its own config (it advertises models through a
    // non-standard `configOptions` field, not ACP `models`), so the client must not
    // fail the session — it leaves the container-pinned model in place.
    const setSessionModel = vi.fn(async () => undefined);

    await selectSessionModel(
      { setSessionModel } as never,
      { sessionId: "session-1" },
      "anthropic/claude-sonnet-4-6",
    );

    expect(setSessionModel).not.toHaveBeenCalled();
  });

  test("fails loudly when the selected model is not advertised", async () => {
    await expect(
      selectSessionModel(
        { setSessionModel: vi.fn() } as never,
        {
          sessionId: "session-1",
          models: {
            currentModelId: "default",
            availableModels: [{ modelId: "default", name: "Default" }],
          },
        },
        "claude-opus-4-8",
      ),
    ).rejects.toThrow("claude-opus-4-8");
  });
});

describe("buildPromptBlocks", () => {
  // The system-context invariant: the rendered context is ALWAYS its own ContentBlock and is
  // never string-merged into the user's message, so the user's text (e.g. a leading `/command`)
  // stays intact. When the agent advertises `embeddedContext`, context uses ACP's native
  // embedded `resource` block; otherwise it falls back to a plain leading `text` block.
  test("embeds context as a leading resource block, user text as its own trailing block", () => {
    expect(buildPromptBlocks("CTX", "hello", { embeddedContext: true })).toEqual([
      {
        type: "resource",
        resource: { uri: "angee:///agent/system-context", text: "CTX", mimeType: "text/markdown" },
      },
      { type: "text", text: "hello" },
    ]);
  });

  test("falls back to a leading text block when the agent lacks embeddedContext", () => {
    const expected = [
      { type: "text", text: "CTX" },
      { type: "text", text: "hello" },
    ];
    expect(buildPromptBlocks("CTX", "hello", null)).toEqual(expected);
    expect(buildPromptBlocks("CTX", "hello", { embeddedContext: false })).toEqual(expected);
  });

  test("omits the context block entirely when there is no context", () => {
    expect(buildPromptBlocks("", "hello", { embeddedContext: true })).toEqual([
      { type: "text", text: "hello" },
    ]);
  });

  test("never merges context into the user text — a leading /command stays at the start of its own block", () => {
    const blocks = buildPromptBlocks("CTX", "/clear keep this", { embeddedContext: true });
    expect(blocks[blocks.length - 1]).toEqual({ type: "text", text: "/clear keep this" });
    expect(JSON.stringify(blocks)).not.toContain("CTX\n\n/clear");
  });
});

