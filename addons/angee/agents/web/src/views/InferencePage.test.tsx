import { CONNECT_CALLBACK_FALLBACK_PATH } from "@angee/integrate";
import { describe, expect, test } from "vitest";

import { inferenceConnectCallbackPath } from "./InferencePage";

describe("inference provider OAuth callback path", () => {
  test("uses the fallback callback alias for Anthropic", () => {
    expect(inferenceConnectCallbackPath({ backendClass: "anthropic" })).toBe(
      CONNECT_CALLBACK_FALLBACK_PATH,
    );
    expect(inferenceConnectCallbackPath({ backendClass: "ANTHROPIC" })).toBe(
      CONNECT_CALLBACK_FALLBACK_PATH,
    );
  });

  test("keeps the canonical callback for non-Anthropic providers", () => {
    expect(inferenceConnectCallbackPath({ backendClass: "openai" })).toBeUndefined();
    expect(inferenceConnectCallbackPath({ backendClass: "deepseek" })).toBeUndefined();
  });
});
