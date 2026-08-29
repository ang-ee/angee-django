import { describe, expect, it } from "vitest";

import { messageOf } from "./acp-error";

describe("messageOf", () => {
  it("reads the message off an Error", () => {
    expect(messageOf(new Error("boom"), "fallback")).toBe("boom");
  });

  // Regression: the ACP connection rejects a failed request with the raw JSON-RPC
  // error object (`reject(response.error)`), not an Error — so an agent-side 401 was
  // dropped for the generic fallback. Read the object's `message` too.
  it("reads the message off a raw JSON-RPC error object", () => {
    const error = {
      code: -32603,
      message: 'Internal error: Failed to authenticate. API Error: 401 {"type":"authentication_error"}',
    };
    expect(messageOf(error, "The agent did not respond.")).toBe(error.message);
  });

  it("falls back for an Error with an empty message", () => {
    expect(messageOf(new Error(""), "fallback")).toBe("fallback");
  });

  it("falls back for a value with no usable message", () => {
    expect(messageOf({ code: -32603 }, "fallback")).toBe("fallback");
    expect(messageOf({ message: 42 }, "fallback")).toBe("fallback");
    expect(messageOf("nope", "fallback")).toBe("fallback");
    expect(messageOf(null, "fallback")).toBe("fallback");
  });
});
