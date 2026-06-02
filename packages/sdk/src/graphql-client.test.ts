import { fetchExchange } from "@urql/core";
import { describe, expect, test, vi } from "vitest";

import {
  createCsrfTokenProvider,
  createUrqlClient,
  graphQLWebSocketUrl,
} from "./graphql-client";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("graphQLWebSocketUrl", () => {
  test("swaps http(s) for ws(s) on an absolute endpoint", () => {
    expect(graphQLWebSocketUrl("https://api.test/graphql/")).toBe(
      "wss://api.test/graphql/",
    );
    expect(graphQLWebSocketUrl("http://api.test/graphql/")).toBe(
      "ws://api.test/graphql/",
    );
  });

  test("resolves a relative endpoint against an origin", () => {
    expect(graphQLWebSocketUrl("/graphql/", "https://app.test")).toBe(
      "wss://app.test/graphql/",
    );
  });
});

describe("createCsrfTokenProvider", () => {
  test("fetches once and caches the token", async () => {
    const fetch = vi.fn(async () => jsonResponse({ token: "TOKEN" }));
    const provider = createCsrfTokenProvider({ fetch });
    expect(await provider.token()).toBe("TOKEN");
    expect(await provider.token()).toBe("TOKEN");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  test("dedupes concurrent fetches", async () => {
    const fetch = vi.fn(async () => jsonResponse({ token: "TOKEN" }));
    const provider = createCsrfTokenProvider({ fetch });
    const [a, b] = await Promise.all([provider.token(), provider.token()]);
    expect([a, b]).toEqual(["TOKEN", "TOKEN"]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  test("refetches after clear", async () => {
    const fetch = vi.fn(async () => jsonResponse({ token: "TOKEN" }));
    const provider = createCsrfTokenProvider({ fetch });
    await provider.token();
    provider.clear();
    await provider.token();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe("createUrqlClient", () => {
  test("sends credentials and the CSRF header on a mutation", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetch = vi.fn(async (url: string, init: RequestInit) => {
      const isCsrf = url.includes("/csrf/");
      calls.push({ url, init });
      return isCsrf
        ? jsonResponse({ token: "CSRF123" })
        : jsonResponse({
            data: {
              saleDelete: { __typename: "DeletePreview", ok: true, id: "1" },
            },
          });
    });
    const client = createUrqlClient({
      url: "/graphql/",
      fetch: fetch as unknown as typeof globalThis.fetch,
      csrfEndpoint: "/csrf/",
      // This case asserts transport (credentials + CSRF header); a fetch-only
      // stack keeps the normalized cache out of the assertion.
      exchanges: [fetchExchange],
    });
    const result = await client
      .mutation("mutation { saleDelete(id: \"1\") { ok id } }", {})
      .toPromise();
    expect(result.error).toBeUndefined();

    const graphqlCall = calls.find((call) => !call.url.includes("/csrf/"));
    expect(graphqlCall?.init.credentials).toBe("include");
    const headers = new Headers(graphqlCall?.init.headers);
    expect(headers.get("x-csrftoken")).toBe("CSRF123");
  });
});
