import { describe, expect, test, vi } from "vitest";
import { createAngeeGraphQLClient, retryableQueryError } from "./provider";
import { ServerConnection, ServerUnavailableError } from "./server-connection";
import { sessionAuth } from "./transport-auth";

describe("shared server connection", () => {
  test.each([502, 503, 504])("blocks every schema after HTTP %s and sends no GraphQL after failed CSRF", async status => {
    const connection = new ServerConnection();
    const network = vi.fn(async () => new Response("upstream detail", { status }));
    const publicClient = createAngeeGraphQLClient({ url: "/graphql/public/", fetch: network, connection });
    const consoleClient = createAngeeGraphQLClient({ url: "/graphql/console/", fetch: network, connection });
    const failure = await publicClient.request("{ __typename }").catch(error => error);
    expect(failure).toBeInstanceOf(ServerUnavailableError);
    expect(retryableQueryError(failure)).toBe(false);
    for (let i = 0; i < 20; i++) {
      await expect(consoleClient.request("{ __typename }")).rejects.toBeInstanceOf(ServerUnavailableError);
    }
    expect(network).toHaveBeenCalledTimes(1);
    expect(network.mock.calls[0]).toEqual(["/auth/csrf/", { credentials: "include" }]);
  });

  test("reports concurrent failures once and never replays a mutation on recovery", async () => {
    const connection = new ServerConnection();
    const changed = vi.fn();
    connection.subscribe(changed);
    const network = vi.fn(async (): Promise<Response> => { throw new TypeError("Failed to fetch"); });
    const fetch = connection.fetch(network);
    await Promise.allSettled([fetch("/graphql/", { method: "POST", body: "mutation" }), fetch("/graphql/")]);
    expect(changed).toHaveBeenCalledTimes(1);
    expect(connection.getSnapshot()).toBe(true);
    connection.restore();
    expect(changed).toHaveBeenCalledTimes(2);
    expect(network).toHaveBeenCalledTimes(2);
    network.mockImplementation(async () => new Response("OK"));
    expect((await fetch("/graphql/")).ok).toBe(true);
    expect(network).toHaveBeenCalledTimes(3);
  });

  test.each([400, 401, 403, 429, 500])("keeps HTTP %s as an ordinary response", async status => {
    const connection = new ServerConnection();
    const response = await connection.fetch(async () => new Response(null, { status }))("/graphql/");
    expect(response.status).toBe(status);
    expect(connection.getSnapshot()).toBe(false);
  });

  test("does not treat cancellation or application exceptions as downtime", async () => {
    const connection = new ServerConnection();
    const controller = new AbortController();
    controller.abort();
    const network = vi.fn(async () => new Response());
    await expect(connection.fetch(network)("/graphql/", { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(network).not.toHaveBeenCalled();
    await expect(connection.fetch(async () => { throw new Error("application bug"); })("/graphql/")).rejects.toThrow("application bug");
    expect(connection.getSnapshot()).toBe(false);
  });

  test("even an unmonitored session transport stops after a non-OK CSRF bootstrap", async () => {
    const network = vi.fn(async () => new Response(null, { status: 403 }));
    await expect(sessionAuth()(network)("/graphql/public/")).rejects.toThrow("secure session");
    expect(network).toHaveBeenCalledTimes(1);
  });
});
