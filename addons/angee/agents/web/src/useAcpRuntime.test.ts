import { describe, expect, test, vi } from "vitest";

import { selectSessionModel } from "./useAcpRuntime";

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

