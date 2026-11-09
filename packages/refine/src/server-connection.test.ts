import { describe, expect, test, vi } from "vitest";
import { createAngeeGraphQLClient, retryableQueryError } from "./provider";
import { ServerConnection, ServerUnavailableError } from "./server-connection";
import { sessionAuth } from "./transport-auth";

describe("shared server connection", () => {
  test.each([502, 503, 504])("HTTP %s asks for a check and stays that request's ordinary, retryable error", async status => {
    const connection = new ServerConnection();
    const network = vi.fn(async () => new Response("upstream detail", { status }));
    const client = createAngeeGraphQLClient({ url: "/graphql/public/", fetch: network, connection });
    const failure = await client.request("{ __typename }").catch(error => error);
    expect(failure).not.toBeInstanceOf(ServerUnavailableError);
    expect(retryableQueryError(failure)).toBe(true);
    expect(connection.getSnapshot()).toBe("checking");
    // The gateway answered the CSRF bootstrap too; the read still went out.
    expect(network).toHaveBeenCalledTimes(2);
  });

  test("a failed check blocks every schema before CSRF until a check restores it", async () => {
    const connection = new ServerConnection();
    const network = vi.fn(async () => Response.json({ data: { __typename: "Query" } }));
    const publicClient = createAngeeGraphQLClient({ url: "/graphql/public/", fetch: network, connection });
    const consoleClient = createAngeeGraphQLClient({ url: "/graphql/console/", fetch: network, connection });
    connection.disconnect();
    const failure = await publicClient.request("{ __typename }").catch(error => error);
    expect(failure).toBeInstanceOf(ServerUnavailableError);
    expect(retryableQueryError(failure)).toBe(false);
    for (let i = 0; i < 20; i++) {
      await expect(consoleClient.request("{ __typename }")).rejects.toBeInstanceOf(ServerUnavailableError);
    }
    expect(network).not.toHaveBeenCalled();
    connection.restore();
    await expect(consoleClient.request("{ __typename }")).resolves.toEqual({ __typename: "Query" });
  });

  test("reports concurrent failures once and never replays a mutation on recovery", async () => {
    const connection = new ServerConnection();
    const changed = vi.fn();
    connection.subscribe(changed);
    const network = vi.fn(async (): Promise<Response> => { throw new TypeError("Failed to fetch"); });
    const fetch = connection.fetch(network);
    const results = await Promise.allSettled([fetch("/graphql/", { method: "POST", body: "mutation" }), fetch("/graphql/")]);
    expect(results.map(result => result.status === "rejected" && result.reason)).toEqual([expect.any(TypeError), expect.any(TypeError)]);
    expect(changed).toHaveBeenCalledTimes(1);
    expect(connection.getSnapshot()).toBe("checking");
    const armedAt = connection.armedAt;
    expect(armedAt).toEqual(expect.any(Number));
    connection.disconnect();
    expect(connection.armedAt).toBe(armedAt);
    await expect(fetch("/graphql/")).rejects.toBeInstanceOf(ServerUnavailableError);
    connection.restore();
    expect(connection.armedAt).toBeNull();
    expect(changed).toHaveBeenCalledTimes(3);
    expect(network).toHaveBeenCalledTimes(2);
    network.mockImplementation(async () => new Response("OK"));
    expect((await fetch("/graphql/")).ok).toBe(true);
    expect(network).toHaveBeenCalledTimes(3);
  });

  test.each([400, 401, 403, 429, 500])("keeps HTTP %s as an ordinary response", async status => {
    const connection = new ServerConnection();
    const response = await connection.fetch(async () => new Response(null, { status }))("/graphql/");
    expect(response.status).toBe(status);
    expect(connection.getSnapshot()).toBe("connected");
    expect(connection.armedAt).toBeNull();
  });

  test("does not treat cancellation or application exceptions as downtime", async () => {
    const connection = new ServerConnection();
    const controller = new AbortController();
    controller.abort();
    const network = vi.fn(async () => new Response());
    await expect(connection.fetch(network)("/graphql/", { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(network).not.toHaveBeenCalled();
    await expect(connection.fetch(async () => { throw new Error("application bug"); })("/graphql/")).rejects.toThrow("application bug");
    expect(connection.getSnapshot()).toBe("connected");
  });

  test("a non-OK CSRF bootstrap sends the request without a token", async () => {
    const network = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(null, { status: input === "/auth/csrf/" ? 403 : 200 }));
    const response = await sessionAuth()(network)("/graphql/public/", { method: "POST" });
    expect(response.ok).toBe(true);
    expect(network).toHaveBeenCalledTimes(2);
    expect(new Headers(network.mock.calls[1]?.[1]?.headers).has("x-csrftoken")).toBe(false);
  });
});
