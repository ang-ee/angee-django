import { describe, expect, test, vi } from "vitest";

import { bearerAuth, bearerAuthFromGetter } from "./transport-auth";

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
