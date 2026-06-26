// @vitest-environment happy-dom

import { afterEach, describe, expect, test } from "vitest";

import {
  CONNECT_CALLBACK_PATH,
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

  test("uses the canonical callback URI when completing the callback", () => {
    window.history.replaceState(null, "", CONNECT_CALLBACK_PATH);

    expect(currentConnectCallbackRedirectUri()).toBe(
      `${window.location.origin}${CONNECT_CALLBACK_PATH}`,
    );
  });
});
