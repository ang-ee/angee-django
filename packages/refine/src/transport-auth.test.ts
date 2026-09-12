import { describe, expect, test, vi } from "vitest";

import { bearerAuth, bearerAuthFromGetter, sessionAuth, createCsrfTokenProvider } from "./transport-auth";

/** Capture the `Authorization` header a wrapped fetch would send. */
function authHeaderFor(
  baseFetch: ReturnType<typeof vi.fn>,
): string | null {
  const init = baseFetch.mock.lastCall?.[1] as RequestInit | undefined;
  return new Headers(init?.headers).get("Authorization");
}

describe("bearerAuth", () => {
  test("sets the fixed token on every request", async () => {
    const baseFetch = vi.fn(async () => new Response());
    const fetchImpl = bearerAuth("fixed-token")(baseFetch);

    await fetchImpl("/graphql");

    expect(authHeaderFor(baseFetch)).toBe("Bearer fixed-token");
  });
});

describe("bearerAuthFromGetter", () => {
  test("reads the current token from the getter on each request", async () => {
    const baseFetch = vi.fn(async () => new Response());
    let token: string | null = "first-token";
    const fetchImpl = bearerAuthFromGetter(() => token)(baseFetch);

    await fetchImpl("/graphql");
    expect(authHeaderFor(baseFetch)).toBe("Bearer first-token");

    token = "rotated-token";
    await fetchImpl("/graphql");
    expect(authHeaderFor(baseFetch)).toBe("Bearer rotated-token");
  });

  test("omits the Authorization header when the getter returns null", async () => {
    const baseFetch = vi.fn(async () => new Response());
    const fetchImpl = bearerAuthFromGetter(() => null)(baseFetch);

    await fetchImpl("/graphql");

    expect(authHeaderFor(baseFetch)).toBeNull();
  });
});

describe("sessionAuth CSRF rotation", () => {
  test("reads the host's current cookie after login instead of retaining a cached token", async () => {
    let cookie = "angee_local_csrf=before-login";
    vi.stubGlobal("document", { get cookie() { return cookie; } });
    const fetchToken = vi.fn(async () => Response.json({ token: "masked", cookieName: "angee_local_csrf" }));
    const send = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response());
    const request = sessionAuth({ fetch: fetchToken })(send);
    try {
      await request("/graphql/public/");
      cookie = "angee_local_csrf=after-login";
      await request("/graphql/console/");
      expect(new Headers(send.mock.lastCall?.[1]?.headers).get("x-csrftoken")).toBe("after-login");
      expect(fetchToken).toHaveBeenCalledTimes(1);
    } finally { vi.unstubAllGlobals(); }
  });

  test("deduplicates concurrent token reads and retries a failed bootstrap", async () => {
    const fetchToken = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(Response.json({ token: "fresh", cookieName: null }));
    const provider = createCsrfTokenProvider({ fetch: fetchToken });
    await expect(provider.token()).rejects.toThrow("offline");
    expect(await Promise.all([provider.token(), provider.token()])).toEqual(["fresh", "fresh"]);
    expect(fetchToken).toHaveBeenCalledTimes(2);
  });
});


test("unreadable CSRF cookies require a fresh token after a session rotates", async () => {
  vi.stubGlobal("document", { cookie: "" });
  const fetchToken = vi.fn()
    .mockResolvedValueOnce(Response.json({ token: "before-login", cookieName: null }))
    .mockResolvedValueOnce(Response.json({ token: "after-login", cookieName: null }));
  try {
    const provider = createCsrfTokenProvider({ fetch: fetchToken });
    expect(await provider.token()).toBe("before-login");
    expect(await provider.token()).toBe("after-login");
    expect(fetchToken).toHaveBeenCalledTimes(2);
  } finally { vi.unstubAllGlobals(); }
});
