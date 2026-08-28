import { describe, expect, test } from "vitest";

import { resolveConnectOptions } from "./env";

describe("resolveConnectOptions", () => {
  test("uses a local browser when no websocket endpoint is configured", () => {
    expect(resolveConnectOptions({ E2E_WS_TOKEN: "unused-token" })).toBeUndefined();
  });

  test("connects to the configured Playwright browser server", () => {
    expect(resolveConnectOptions({
      E2E_WS_ENDPOINT: "ws://localhost:8081/playwright-server/",
    })).toEqual({
      wsEndpoint: "ws://localhost:8081/playwright-server/",
    });
  });

  test("authenticates the websocket handshake through the operator edge", () => {
    expect(resolveConnectOptions({
      E2E_WS_ENDPOINT: "ws://localhost:8081/playwright-server/",
      E2E_WS_TOKEN: "route-token",
    })).toEqual({
      wsEndpoint: "ws://localhost:8081/playwright-server/",
      headers: { Authorization: "Bearer route-token" },
    });
  });
});
