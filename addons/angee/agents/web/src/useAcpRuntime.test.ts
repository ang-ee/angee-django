import { describe, expect, test, vi } from "vitest";
import type { CompleteAttachment } from "@assistant-ui/react";

import {
  attachmentBlocks,
  buildPromptBlocks,
  dataUrlToImageBlock,
  selectSessionModel,
} from "./useAcpRuntime";

// A complete image attachment as `SimpleImageAttachmentAdapter` yields it: an `image` part whose
// `image` is a `data:<mime>;base64,<data>` URL. The reducer only reads `content`, so the rest is
// cast at this boundary.
function imageAttachment(dataUrl: string): CompleteAttachment {
  return { content: [{ type: "image", image: dataUrl }] } as unknown as CompleteAttachment;
}

describe("selectSessionModel", () => {
  // In sdk 1.0.0 the model is a "model"-category select config option, applied via
  // setSessionConfigOption; the session fixtures below mirror that shape.
  const modelOption = (currentValue: string, values: string[]) => ({
    id: "model",
    type: "select" as const,
    category: "model" as const,
    name: "Model",
    currentValue,
    options: values.map((value) => ({ value, name: value })),
  });

  test("sets the model config option to the agent model handle", async () => {
    const setSessionConfigOption = vi.fn(async () => undefined);

    await selectSessionModel(
      { setSessionConfigOption } as never,
      { sessionId: "session-1", configOptions: [modelOption("default", ["default", "claude-opus-4-8"])] } as never,
      "claude-opus-4-8",
    );

    expect(setSessionConfigOption).toHaveBeenCalledWith({
      sessionId: "session-1",
      configId: "model",
      value: "claude-opus-4-8",
    });
  });

  test("does not set when the selected model is already current", async () => {
    const setSessionConfigOption = vi.fn(async () => undefined);

    await selectSessionModel(
      { setSessionConfigOption } as never,
      { sessionId: "session-1", configOptions: [modelOption("claude-opus-4-8", ["claude-opus-4-8"])] } as never,
      "claude-opus-4-8",
    );

    expect(setSessionConfigOption).not.toHaveBeenCalled();
  });

  test("defers when the agent advertises no model config option", async () => {
    // The agent owns its model (env/config-pinned in its container), so the client must not
    // fail the session — it leaves the configured model in place.
    const setSessionConfigOption = vi.fn(async () => undefined);

    await selectSessionModel(
      { setSessionConfigOption } as never,
      { sessionId: "session-1" } as never,
      "anthropic/claude-sonnet-4-6",
    );

    expect(setSessionConfigOption).not.toHaveBeenCalled();
  });

  test("fails loudly when the selected model is not available", async () => {
    await expect(
      selectSessionModel(
        { setSessionConfigOption: vi.fn() } as never,
        { sessionId: "session-1", configOptions: [modelOption("default", ["default"])] } as never,
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

  // claude-agent-acp runs a slash command only when the message is a clean "/command"; any extra
  // block makes the SDK treat it as prose. So a /command send carries NO context block.
  test("a /command send carries no context block — a clean command for the agent to run", () => {
    expect(buildPromptBlocks("CTX", "/context", { embeddedContext: true })).toEqual([
      { type: "text", text: "/context" },
    ]);
  });

  // Phase 3: attachment blocks ride after [context?, userText?]; the 4th param defaults to []
  // so every 3-arg caller above stays unchanged.
  test("appends attachment blocks after the context and user-text blocks", () => {
    const image = { type: "image" as const, data: "AAA", mimeType: "image/png" };
    expect(buildPromptBlocks("CTX", "hi", { embeddedContext: true }, [image])).toEqual([
      {
        type: "resource",
        resource: { uri: "angee:///agent/system-context", text: "CTX", mimeType: "text/markdown" },
      },
      { type: "text", text: "hi" },
      image,
    ]);
  });

  test("omits the empty user-text block for an image-only send (no context)", () => {
    const image = { type: "image" as const, data: "AAA", mimeType: "image/png" };
    expect(buildPromptBlocks("", "", { image: true }, [image])).toEqual([image]);
  });
});

describe("attachmentBlocks", () => {
  test("maps an image data-URL to an ACP image block when the agent supports image", () => {
    expect(
      attachmentBlocks([imageAttachment("data:image/png;base64,AAA")], { image: true }),
    ).toEqual([{ type: "image", data: "AAA", mimeType: "image/png" }]);
  });

  test("returns [] when the agent does not advertise image (or has no capabilities)", () => {
    const attachments = [imageAttachment("data:image/png;base64,AAA")];
    expect(attachmentBlocks(attachments, null)).toEqual([]);
    expect(attachmentBlocks(attachments, { image: false })).toEqual([]);
    expect(attachmentBlocks(undefined, { image: true })).toEqual([]);
  });

  test("skips non-image (file) parts — the resource_link path is deferred", () => {
    const fileAttachment = {
      content: [{ type: "file", filename: "notes.pdf", data: "ZZZ", mimeType: "application/pdf" }],
    } as unknown as CompleteAttachment;
    expect(attachmentBlocks([fileAttachment], { image: true })).toEqual([]);
  });
});

describe("dataUrlToImageBlock", () => {
  test("splits a base64 data URL into raw base64 data + mime type", () => {
    expect(dataUrlToImageBlock("data:image/jpeg;base64,/9j/4AAQ")).toEqual({
      type: "image",
      data: "/9j/4AAQ",
      mimeType: "image/jpeg",
    });
  });

  test("returns null for a non-base64-data-URL string", () => {
    expect(dataUrlToImageBlock("https://example.com/cat.png")).toBeNull();
  });
});

