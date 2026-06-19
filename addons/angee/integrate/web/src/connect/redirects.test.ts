// @vitest-environment happy-dom

import { afterEach, describe, expect, test } from "vitest";

import {
  CONNECT_CALLBACK_FALLBACK_PATH,
  CONNECT_CALLBACK_PATH,
  connectCallbackPathForRecord,
  connectCallbackRedirectUri,
  currentConnectCallbackRedirectUri,
} from "./redirects";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("connect callback redirects", () => {
  test("builds the canonical callback URI by default", () => {
    expect(connectCallbackRedirectUri()).toBe(
      `${window.location.origin}${CONNECT_CALLBACK_PATH}`,
    );
  });

  test("builds the fallback callback URI when requested", () => {
    expect(connectCallbackRedirectUri(CONNECT_CALLBACK_FALLBACK_PATH)).toBe(
      `${window.location.origin}${CONNECT_CALLBACK_FALLBACK_PATH}`,
    );
  });

  test("uses the mounted fallback route when completing the callback", () => {
    window.history.replaceState(null, "", CONNECT_CALLBACK_FALLBACK_PATH);

    expect(currentConnectCallbackRedirectUri()).toBe(
      `${window.location.origin}${CONNECT_CALLBACK_FALLBACK_PATH}`,
    );
  });

  test("selects the fallback callback for Anthropic connect records", () => {
    expect(connectCallbackPathForRecord({ backendClass: "anthropic" })).toBe(
      CONNECT_CALLBACK_FALLBACK_PATH,
    );
    expect(connectCallbackPathForRecord({ vendorLabel: "Anthropic" })).toBe(
      CONNECT_CALLBACK_FALLBACK_PATH,
    );
    expect(
      connectCallbackPathForRecord({ vendor: { slug: "anthropic" } }),
    ).toBe(CONNECT_CALLBACK_FALLBACK_PATH);
  });

  test("keeps the canonical callback for other connect records", () => {
    expect(connectCallbackPathForRecord({ backendClass: "openai" })).toBeUndefined();
    expect(connectCallbackPathForRecord({ vendorLabel: "GitHub" })).toBeUndefined();
  });
});
