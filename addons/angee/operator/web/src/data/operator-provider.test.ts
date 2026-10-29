// @vitest-environment happy-dom
import { afterEach, describe, expect, test, vi } from "vitest";

import { operatorToken } from "./operator-token";

// Spy on the shared client builder so the test asserts the wiring (url + the
// auth fetch wrapper) without a live daemon. `createOperatorDataProvider` is the
// only caller, so a captured `options` is the provider's exact build input.
const captured = vi.hoisted(() => ({ options: null as Record<string, unknown> | null }));

vi.mock("@angee/refine", async () => {
  const actual = await vi.importActual<typeof import("@angee/refine")>("@angee/refine");
  return {
    ...actual,
    createAngeeHasuraDataProvider: (options: Record<string, unknown>) => {
      captured.options = options;
      return {} as never;
    },
  };
});

import { createOperatorDataProvider } from "./operator-provider";

afterEach(() => {
  operatorToken.set(null);
  captured.options = null;
});

describe("createOperatorDataProvider", () => {
  test("reads the configured connection endpoint at request time", () => {
    createOperatorDataProvider();
    const endpoint = captured.options?.url as () => string;
    expect(() => endpoint()).toThrow("unavailable");
    operatorToken.set("first", "/ops/graphql");
    expect(endpoint()).toBe("/ops/graphql");
    operatorToken.set("second", "https://daemon.example.test/graphql");
    expect(endpoint()).toBe("https://daemon.example.test/graphql");
  });

  test("carries the live bearer per request, picking up rotation", async () => {
    createOperatorDataProvider();
    const auth = captured.options?.auth as (
      base: typeof globalThis.fetch,
    ) => typeof globalThis.fetch;

    const seen: (string | null)[] = [];
    const baseFetch = ((_input: unknown, init?: RequestInit) => {
      seen.push(new Headers(init?.headers).get("Authorization"));
      return Promise.resolve(new Response("{}"));
    }) as unknown as typeof globalThis.fetch;
    const fetchImpl = auth(baseFetch);

    operatorToken.set("first-token");
    await fetchImpl("/operator/graphql");
    operatorToken.set("rotated-token");
    await fetchImpl("/operator/graphql");
    operatorToken.set(null);
    await fetchImpl("/operator/graphql");

    expect(seen).toEqual([
      "Bearer first-token",
      "Bearer rotated-token",
      null,
    ]);
  });
});
