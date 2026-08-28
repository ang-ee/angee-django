// @vitest-environment happy-dom

import { afterEach, describe, expect, test } from "vitest";

import {
  CONNECT_CALLBACK_LOOPBACK_PATH,
  CONNECT_CALLBACK_PATH,
  connectCallbackRedirectUri,
  currentConnectCallbackRedirectUri,
} from "./redirects";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("connect callback redirects", () => {
  test("proposes the canonical callback URI at connect-start", () => {
    expect(connectCallbackRedirectUri()).toBe(
      `${window.location.origin}${CONNECT_CALLBACK_PATH}`,
    );
  });

  test("completes on the canonical callback route at its own URL", () => {
    window.history.replaceState(null, "", CONNECT_CALLBACK_PATH);

    expect(currentConnectCallbackRedirectUri()).toBe(
      `${window.location.origin}${CONNECT_CALLBACK_PATH}`,
    );
  });

  test("completes on the loopback `/callback` alias at its own URL", () => {
    // A fixed public client (e.g. Anthropic) returns to the bare loopback path on
    // localhost; completion must use that exact path as the redirect_uri, not the canonical.
    window.history.replaceState(null, "", CONNECT_CALLBACK_LOOPBACK_PATH);

    expect(currentConnectCallbackRedirectUri()).toBe(
      `${window.location.origin}${CONNECT_CALLBACK_LOOPBACK_PATH}`,
    );
  });
});
