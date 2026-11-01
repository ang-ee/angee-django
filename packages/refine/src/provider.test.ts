import { afterEach, describe, expect, test, vi } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { parse } from "graphql";
import { keys } from "@refinedev/core";
import type { AngeeLiveResource } from "./provider";

import {
  ANGEE_HASURA_PROVIDER_OPTIONS,
  boundedGraphQLTransportError,
  publicGraphQLError,
  createAngeeGraphQLClient,
  createAngeeHasuraDataProvider,
  createAngeeChangeLiveProvider,
  resolveGraphQLWebSocketEndpoint,
  retryableQueryError,
} from "./provider";

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function invalidationClient(invalidateQueries: QueryClient["invalidateQueries"]) {
  const client = new QueryClient();
  return {
    invalidateQueries,
    cancelQueries: vi.fn(async () => undefined),
    getQueryCache: () => client.getQueryCache(),
  };
}

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("Angee Hasura provider defaults", () => {
  test("sends typed creation and revision arguments through native CRUD documents", async () => {
    const requests: { query: string; variables: Record<string, unknown> }[] = [];
    const provider = createAngeeHasuraDataProvider({
      url: "https://example.invalid/graphql", auth: (request) => request,
      mutations: { notes: {
        create: { root: "insert_notes_one", inputType: "notes_insert_input", arguments: [{ name: "idempotency_key", type: "String!" }] },
        update: { root: "update_notes_by_pk", inputType: "notes_set_input", arguments: [{ name: "expected_revision", type: "Int!" }] },
      } },
      fetch: async (_input, init) => {
        requests.push(JSON.parse(String(init?.body)));
        return jsonResponse({ insert_notes_one: { id: "note-1" }, update_notes_by_pk: { id: "note-1", revision: 4 } });
      },
    });
    const created = await provider.create({ resource: "notes", variables: { title: "New" },
      meta: { fields: ["id"], gqlVariables: { idempotency_key: "session-key", expected_revision: 99 } } });
    expect(created.data.id).toBe("note-1");
    const updated = await provider.update({ resource: "notes", id: "note-1", variables: { title: "Changed" },
      meta: { fields: ["id", "revision"], gqlVariables: { expected_revision: 3 } } });
    expect(updated.data.revision).toBe(4);
    expect(requests[0]?.variables).toMatchObject({ object: { title: "New" }, idempotency_key: "session-key" });
    expect(requests[0]?.query).toContain("$idempotency_key: String!");
    expect(requests[0]?.variables).not.toHaveProperty("expected_revision");
    expect(requests[1]?.variables).toMatchObject({ object: { title: "Changed" }, pk_columns: { id: "note-1" }, expected_revision: 3 });
    expect(requests[1]?.query).toContain("$expected_revision: Int!");
    expect(requests[1]?.query).toContain("expected_revision: $expected_revision");
  });

  test.each(["STALE_REVISION", "CREATION_KEY_CONFLICT", "VIEW_AS_READ_ONLY"])("preserves the public %s code", (code) => {
    const error = boundedGraphQLTransportError({ response: { errors: [{ message: "Write refused.", extensions: { code } }] } });
    expect(error).toMatchObject({ graphQLErrors: [{ extensions: { code } }] });
  });

  test("fails on an unknown advertised argument before sending a write", async () => {
    const fetch = vi.fn();
    const provider = createAngeeHasuraDataProvider({
      url: "https://example.invalid/graphql",
      auth: (request) => request,
      mutations: { notes: { create: {
        root: "insert_notes_one", inputType: "notes_insert_input",
        arguments: [{ name: "unknown_argument", type: "String" }],
      } } },
      fetch,
    });
    await expect(async () => provider.create({ resource: "notes", variables: {} }))
      .rejects.toThrow('Unknown mutation root argument "unknown_argument"');
    expect(fetch).not.toHaveBeenCalled();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("pins the stock provider to Angee's Hasura dialect", () => {
    expect(ANGEE_HASURA_PROVIDER_OPTIONS).toEqual({
      idType: "String",
      namingConvention: "hasura-default",
    });
  });

  test("gives projection-less native list reads a valid identity selection", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as { query: string };
      expect(body.query).toMatch(/channels[^{}]*\{\s*id\s*\}/);
      return new Response(JSON.stringify({
        data: { channels: [], channels_aggregate: { aggregate: { count: 0 } } },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const provider = createAngeeHasuraDataProvider({
      url: "https://example.invalid/graphql",
      auth: (request) => request,
      fetch,
    });

    await provider.getList({
      resource: "channels",
      filters: [],
      sorters: [],
      pagination: { mode: "server", currentPage: 1, pageSize: 10 },
    });
    expect(fetch).toHaveBeenCalledOnce();
  });

  test.each([undefined, { fields: [] }])("gives native detail and multi-record reads an identity selection: %j", async (meta) => {
    const queries: string[] = [];
    const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const { query } = JSON.parse(String(init?.body)) as { query: string };
      queries.push(query);
      expect(() => parse(query)).not.toThrow();
      return jsonResponse({ channels: [{ id: "chn_1" }], channels_by_pk: { id: "chn_1" } });
    });
    const provider = createAngeeHasuraDataProvider({ url: "https://example.invalid/graphql", auth: (request) => request, fetch });
    expect((await provider.getOne({ resource: "channels", id: "chn_1", meta })).data).toEqual({ id: "chn_1" });
    expect((await provider.getMany({ resource: "channels", ids: ["chn_1"], meta })).data).toEqual([{ id: "chn_1" }]);
    for (const query of queries) expect(query).toMatch(/channels(?:_by_pk)?[^{}]*\{\s*id\s*\}/);
  });

  test("repairs an explicitly empty list projection", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as { query: string };
      expect(body.query).toMatch(/channels[^{}]*\{\s*id\s*\}/);
      return jsonResponse({ channels: [], channels_aggregate: { aggregate: { count: 0 } } });
    });
    const provider = createAngeeHasuraDataProvider({
      url: "https://example.invalid/graphql", auth: (request) => request, fetch,
    });
    await provider.getList({
      resource: "channels", filters: [], sorters: [],
      pagination: { mode: "server", currentPage: 1, pageSize: 10 }, meta: { fields: [] },
    });
  });

  test.each([undefined, [], ["id"]])("forwards native cancellation through projection normalization %j", async (fields) => {
    const signal = new AbortController().signal;
    const readSignal = vi.fn(() => signal);
    const meta = { fields, get signal() { return readSignal(); } };
    const provider = createAngeeHasuraDataProvider({
      url: "https://example.invalid/graphql", auth: (request) => request,
      fetch: async (_input, init) => {
        expect(init?.signal).toBe(signal);
        return jsonResponse({
          channels: [{ id: "chn_1" }], channels_by_pk: { id: "chn_1" },
          channels_aggregate: { aggregate: { count: 1 } },
        });
      },
    });
    await provider.getList({ resource: "channels", meta });
    await provider.getOne({ resource: "channels", id: "chn_1", meta });
    await provider.getMany({ resource: "channels", ids: ["chn_1"], meta });
    expect(readSignal).toHaveBeenCalledTimes(3);
    expect(meta.fields).toBe(fields);
  });

  test("preserves explicit projections and authored list documents", async () => {
    const queries: string[] = [];
    const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      queries.push((JSON.parse(String(init?.body)) as { query: string }).query);
      return jsonResponse({ channels: [], channels_aggregate: { aggregate: { count: 0 } } });
    });
    const provider = createAngeeHasuraDataProvider({
      url: "https://example.invalid/graphql", auth: (request) => request, fetch,
    });
    const request = {
      resource: "channels", filters: [], sorters: [],
      pagination: { mode: "server" as const, currentPage: 1, pageSize: 10 },
    };
    await provider.getList({ ...request, meta: { fields: ["display_name"] } });
    const authored = parse(`query AuthoredChannels { channels { backend_class } channels_aggregate { aggregate { count } } }`);
    await provider.getList({ ...request, meta: { gqlQuery: authored, gqlVariables: {} } });

    expect(queries[0]).toMatch(/channels[^{}]*\{\s*display_name\s*\}/);
    expect(queries[0]).not.toMatch(/channels[^{}]*\{\s*id\s*\}/);
    expect(queries[1]).toContain("query AuthoredChannels");
    expect(queries[1]).toContain("backend_class");
  });

  test("bounds native transport errors while preserving safe validation fields", () => {
    const sentinel = "request-variable-secret";
    const normalized = boundedGraphQLTransportError({
      request: { variables: { token: sentinel } },
      response: {
        status: 400,
        errors: [{
          message: "Fix the highlighted fields.",
          extensions: {
            code: "VALIDATION",
            validationErrors: { "config.local_root": ["Required."] },
            formErrors: [],
            debug: sentinel,
          },
        }],
      },
    }) as Error & { response: Record<string, unknown> };

    expect(normalized.message).toBe("Fix the highlighted fields.");
    expect(normalized.response).toEqual({
      status: 400,
      errors: [{
        message: "Fix the highlighted fields.",
        extensions: {
          code: "VALIDATION",
          validationErrors: { "config.local_root": ["Required."] },
          formErrors: [],
        },
      }],
    });
    expect(JSON.stringify(normalized)).not.toContain(sentinel);
  });

  test("drops request metadata and unexpected GraphQL messages", () => {
    const sentinel = "unexpected-secret";
    const normalized = boundedGraphQLTransportError({
      request: { query: sentinel },
      response: { status: 502, errors: [{ message: sentinel, extensions: { code: "INTERNAL" } }] },
    });
    expect(normalized.message).toBe("Request failed.");
    expect(JSON.stringify(normalized)).not.toContain(sentinel);
  });

  test("an admin client keeps every server message and its extensions, still without request metadata", () => {
    const failure = {
      request: { query: "request-secret" },
      response: { status: 200, errors: [{
        message: 'Sync base for workspace "src": fetching source "arp" failed: ssh is not available.',
        extensions: { code: "GIT_FAILED", cause: "ssh_unavailable", hint: "Mount the deploy SSH config." },
      }] },
    };
    const normalized = boundedGraphQLTransportError(failure, "all");
    expect(normalized.message).toBe('Sync base for workspace "src": fetching source "arp" failed: ssh is not available.');
    expect(normalized).toMatchObject({ graphQLErrors: [{ extensions: { code: "GIT_FAILED", cause: "ssh_unavailable" } }] });
    expect(JSON.stringify(normalized)).not.toContain("request-secret");
    // The console default still hides it.
    expect(boundedGraphQLTransportError(failure).message).toBe("Request failed.");
  });

  test("bounds plain network errors", () => {
    expect(boundedGraphQLTransportError(new Error("fetch https://secret.invalid failed")).message)
      .toBe("Request failed.");
  });

  test("normalizes graphql-request failures in the native response middleware", async () => {
    const sentinel = "middleware-request-secret";
    const client = createAngeeGraphQLClient({
      url: "https://example.invalid/graphql",
      auth: (fetch) => fetch,
      fetch: async () => new Response(JSON.stringify({
        errors: [{ message: sentinel, extensions: { code: "INTERNAL", debug: sentinel } }],
      }), { status: 502, headers: { "Content-Type": "application/json" } }),
    });

    const caught = await client.request("query Secret($token: String!) { value }", { token: sentinel })
      .catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(Error);
    expect((caught as Error).message).toBe("Request failed.");
    expect(JSON.stringify(caught)).not.toContain(sentinel);
  });

  test("derives GraphQL WebSocket endpoints from HTTP endpoints", () => {
    expect(resolveGraphQLWebSocketEndpoint("/graphql/console/", "https://app.test")).toBe(
      "wss://app.test/graphql/console/",
    );
  });

  test("preserves explicit WebSocket endpoints", () => {
    expect(resolveGraphQLWebSocketEndpoint("wss://operator.test/graphql")).toBe(
      "wss://operator.test/graphql",
    );
  });

  test("subscribes to backend-declared change roots as refine live events", () => {
    const dispose = vi.fn();
    const subscribe = vi.fn((_payload, sink) => {
      sink.next({
        data: {
          noteChanged: {
            model: "notes.Note",
            id: "note_123",
            action: "update",
            changedFields: ["title"],
            changedValues: { title: "Draft" },
          },
        },
      });
      return dispose;
    });
    const callback = vi.fn();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "noteChanged" })],
    );

    const subscription = provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback,
      params: { resource: "notes" },
    });
    provider.unsubscribe(subscription);

    expect(subscribe).toHaveBeenCalledWith(
      {
        query: "subscription angee_noteChanged { noteChanged { model id action relatedRecords: related_records { model id } changedFields: changed_fields changedValues: changed_values } }",
      },
      expect.any(Object),
    );
    expect(callback).toHaveBeenCalledWith(
      expect.objectContaining({
        channel: "resources/notes",
        type: "updated",
        payload: {
          id: "note_123",
          ids: ["note_123"],
          model: "notes.Note",
          action: "update",
          changedFields: ["title"],
          changedValues: { title: "Draft" },
        },
        meta: { dataProviderName: "console" },
      }),
    );
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  test("invalidates authored query metadata for live model changes", async () => {
    const { subscribe, sinks } = recordingClient();
    const invalidateQueries = vi.fn();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "decisionChanged", list: "workflow_decisions", model: "workflows.Decision" })],
      { queryClient: invalidationClient(invalidateQueries) },
    );

    provider.subscribe({
      channel: "resources/workflow_decisions",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "workflow_decisions" },
    });
    vi.useFakeTimers();
    nthSink(sinks, 0).next({
      data: {
        decisionChanged: {
          model: "workflows.Decision",
          id: "dec_1",
          action: "update",
        },
      },
    });
    expect(invalidateQueries).toHaveBeenCalledWith(expect.objectContaining({ refetchType: "none" }));
    await vi.advanceTimersByTimeAsync(300);

    await vi.waitFor(() => expect(invalidateQueries).toHaveBeenCalledWith({
      predicate: expect.any(Function),
      type: "all",
      refetchType: "active",
    }));
    const predicate = invalidateQueries.mock.calls.find(([filters]) => filters.refetchType === "active")?.[0]?.predicate as
      | ((query: { meta: unknown }) => boolean)
      | undefined;
    expect(predicate?.({ meta: { angeeModels: ["workflows.Decision"] } })).toBe(true);
    expect(predicate?.({ meta: { angeeModels: ["workflows.StepRun"] } })).toBe(false);
  });

  test("skips resources without change roots", () => {
    const subscribe = vi.fn();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: null })],
    );

    const subscription = provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "notes" },
    });
    provider.unsubscribe(subscription);

    expect(subscribe).not.toHaveBeenCalled();
  });

  test("server readiness restarts stale first reads and recovers retained rows after resume", async () => {
    vi.useFakeTimers();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const attempts: { signal: AbortSignal; resolve: (rows: string[]) => void }[] = [];
    const queryFn = vi.fn(({ signal }: { signal: AbortSignal }) => new Promise<string[]>((resolve) => {
      attempts.push({ signal, resolve });
    }));
    const observer = new QueryObserver(client, {
      queryKey: ["notes"], queryFn, meta: { angeeModels: ["notes.Note"] },
    });
    const stopObserver = observer.subscribe(() => undefined);
    const { subscribe, sinks } = recordingClient();
    const on = vi.fn(() => () => undefined);
    const provider = createAngeeChangeLiveProvider({ subscribe, on } as never,
      [resource({ changes: "noteChanged" })], { queryClient: client });
    const callback = vi.fn();
    const subscription = provider.subscribe({ channel: "notes", types: ["*"], callback, params: { resource: "notes" } });
    const ready = { data: { noteChanged: { model: "notes.Note", id: "", action: "ready" } } };
    try {
      expect(queryFn).toHaveBeenCalledOnce();
      // A connection ack has no authority to restart reads. Only server ready does.
      expect(on).not.toHaveBeenCalled();
      nthSink(sinks, 0).next(ready);
      expect(attempts[0]?.signal.aborted).toBe(true);
      await vi.advanceTimersByTimeAsync(300);
      expect(queryFn).toHaveBeenCalledTimes(2);
      attempts[0]?.resolve(["obsolete"]);
      attempts[1]?.resolve(["fresh"]);
      await vi.advanceTimersByTimeAsync(0);
      expect(observer.getCurrentResult().data).toEqual(["fresh"]);
      expect(callback).not.toHaveBeenCalled();
      provider.setEnabled(false);
      provider.setEnabled(true);
      nthSink(sinks, 0).next(ready); // disposed subscription cannot recover the new actor
      expect(queryFn).toHaveBeenCalledTimes(2);
      nthSink(sinks, 1).next(ready);
      await vi.advanceTimersByTimeAsync(300);
      attempts[2]?.resolve(["recovered"]);
      await vi.advanceTimersByTimeAsync(0);
      expect(observer.getCurrentResult().data).toEqual(["recovered"]);
    } finally {
      provider.setEnabled(false);
      attempts.forEach((attempt) => attempt.resolve([]));
      provider.unsubscribe(subscription);
      stopObserver();
      client.clear();
    }
  });

  test("related parent effects coalesce native projections and preserve unrelated detail rows", async () => {
    vi.useFakeTimers();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const parent = keys().data("console").resource("console:notes.Parent");
    const reads = [vi.fn(async () => "new parent"), vi.fn(async () => "unrelated"), vi.fn(async () => "new list")];
    const queryKeys = [parent.action("one").id("parent-1").params({ fields: ["id"] }).get(),
      parent.action("one").id("parent-2").get(), parent.action("list").get()];
    const observers = queryKeys.map((queryKey, index) => {
      client.setQueryData(queryKey, "retained");
      return new QueryObserver(client, { queryKey, queryFn: reads[index]! });
    });
    const stops = observers.map((observer) => observer.subscribe(() => undefined));
    const { subscribe, sinks } = recordingClient();
    const provider = createAngeeChangeLiveProvider({ subscribe, on: vi.fn() } as never, [
      resource({ changes: "noteChanged" }),
      { ...resource({ changes: "parentChanged", list: "parents", model: "notes.Parent" }), resourceIdentifier: "console:notes.Parent" },
    ], { queryClient: client });
    const subscription = provider.subscribe({ channel: "notes", types: ["*"], callback: vi.fn(), params: { resource: "notes" } });
    try {
      const event = { data: { noteChanged: { model: "notes.Note", id: "note-1", action: "update",
        relatedRecords: [{ model: "notes.Parent", id: "parent-1" }] } } };
      nthSink(sinks, 0).next(event);
      nthSink(sinks, 0).next(event);
      await vi.advanceTimersByTimeAsync(300);
      expect(reads[0]).toHaveBeenCalledOnce();
      expect(reads[1]).not.toHaveBeenCalled();
      expect(reads[2]).toHaveBeenCalledOnce();
    } finally { provider.setEnabled(false); provider.unsubscribe(subscription); stops.forEach((stop) => stop()); client.clear(); }
  });

  test("shares one upstream subscription across consumers for the same resource", () => {
    const { subscribe, sinks } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "noteChanged" })],
    );
    const first = vi.fn();
    const second = vi.fn();

    const subA = provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback: first,
      params: { resource: "notes" },
    });
    const subB = provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback: second,
      params: { resource: "notes" },
    });

    expect(subscribe).toHaveBeenCalledTimes(1);

    nthSink(sinks, 0).next({
      data: { noteChanged: { model: "notes.Note", id: "note_1", action: "update" } },
    });
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);

    provider.unsubscribe(subA);
    expect(nthSink(sinks, 0).dispose).not.toHaveBeenCalled();

    provider.unsubscribe(subB);
    expect(nthSink(sinks, 0).dispose).toHaveBeenCalledTimes(1);
  });

  test("reopens the upstream subscription after the last consumer leaves", () => {
    const { subscribe } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "noteChanged" })],
    );

    provider.unsubscribe(
      provider.subscribe({
        channel: "resources/notes",
        types: ["*"],
        callback: vi.fn(),
        params: { resource: "notes" },
      }),
    );
    expect(subscribe).toHaveBeenCalledTimes(1);

    provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "notes" },
    });
    expect(subscribe).toHaveBeenCalledTimes(2);
  });

  test("pauses existing and newly mounted consumers, drops late events, then reopens once", () => {
    const { subscribe, sinks } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "noteChanged" })],
    );
    const callback = vi.fn();
    const params = { channel: "resources/notes", types: ["*"], callback, params: { resource: "notes" } };
    const first = provider.subscribe(params);
    provider.setEnabled(false);
    const second = provider.subscribe(params);
    expect(nthSink(sinks, 0).dispose).toHaveBeenCalledTimes(1);
    expect(subscribe).toHaveBeenCalledTimes(1);
    const event = { data: { noteChanged: { model: "notes.Note", id: "note_1", action: "update" } } };
    nthSink(sinks, 0).next(event);
    expect(callback).not.toHaveBeenCalled();
    provider.setEnabled(true);
    expect(subscribe).toHaveBeenCalledTimes(2);
    nthSink(sinks, 0).next(event);
    expect(callback).not.toHaveBeenCalled();
    nthSink(sinks, 1).next(event);
    expect(callback).toHaveBeenCalledTimes(2);
    provider.unsubscribe(first);
    provider.unsubscribe(second);
    expect(nthSink(sinks, 1).dispose).toHaveBeenCalledTimes(1);
  });

  test("logs and drops errored subscriptions so the next subscriber reconnects", () => {
    const { subscribe, sinks } = recordingClient();
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "noteChanged" })],
    );

    const subA = provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "notes" },
    });
    nthSink(sinks, 0).error(new Error("subscription rejected"));
    provider.unsubscribe(subA);

    provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "notes" },
    });

    expect(subscribe).toHaveBeenCalledTimes(2);
    expect(nthSink(sinks, 0).dispose).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledWith(
      "Angee live subscription failed; the next subscriber will reconnect.",
      expect.objectContaining({ changesRoot: "noteChanged" }),
      expect.any(Error),
    );
  });

  test("subscribes each authored-query model to its own changes root", () => {
    const { subscribe, sinks } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [
        resource({ changes: "noteChanged" }),
        resource({ changes: "tagChanged", list: "tags", model: "notes.Tag" }),
      ],
    );

    const subscription = provider.subscribe({
      channel: "angee/authored/notes.Note,notes.Tag",
      types: ["*"],
      callback: vi.fn(),
      params: { models: ["notes.Note", "notes.Tag"] },
    });

    expect(subscribe).toHaveBeenCalledTimes(2);
    expect(subscribe).toHaveBeenCalledWith(
      {
        query:
          "subscription angee_noteChanged { noteChanged { model id action relatedRecords: related_records { model id } changedFields: changed_fields changedValues: changed_values } }",
      },
      expect.any(Object),
    );

    provider.unsubscribe(subscription);
    expect(nthSink(sinks, 0).dispose).toHaveBeenCalledTimes(1);
    expect(nthSink(sinks, 1).dispose).toHaveBeenCalledTimes(1);
  });

  test("joins the shared fan-out — a resource hook and an authored query share one upstream", () => {
    const { subscribe, sinks } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "noteChanged" })],
    );

    const resourceSub = provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "notes" },
    });
    const authoredSub = provider.subscribe({
      channel: "angee/authored/notes.Note",
      types: ["*"],
      callback: vi.fn(),
      params: { models: ["notes.Note"] },
    });

    // Two consumers (a resource hook and an authored query), one upstream socket.
    expect(subscribe).toHaveBeenCalledTimes(1);

    provider.unsubscribe(resourceSub);
    expect(nthSink(sinks, 0).dispose).not.toHaveBeenCalled();
    provider.unsubscribe(authoredSub);
    expect(nthSink(sinks, 0).dispose).toHaveBeenCalledTimes(1);
  });

  test("invalidates authored reads when a change arrives on a model subscription", async () => {
    const { subscribe, sinks } = recordingClient();
    const invalidateQueries = vi.fn();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "noteChanged" })],
      { queryClient: invalidationClient(invalidateQueries) },
    );

    provider.subscribe({
      channel: "angee/authored/notes.Note",
      types: ["*"],
      callback: vi.fn(),
      params: { models: ["notes.Note"] },
    });
    vi.useFakeTimers();
    nthSink(sinks, 0).next({
      data: { noteChanged: { model: "notes.Note", id: "note_1", action: "update" } },
    });
    await vi.advanceTimersByTimeAsync(300);

    await vi.waitFor(() => expect(invalidateQueries).toHaveBeenCalledWith({
      predicate: expect.any(Function),
      type: "all",
      refetchType: "active",
    }));
    const predicate = invalidateQueries.mock.calls.find(([filters]) => filters.refetchType === "active")?.[0]?.predicate as
      | ((query: { meta: unknown }) => boolean)
      | undefined;
    expect(predicate?.({ meta: { angeeModels: ["notes.Note"] } })).toBe(true);
    expect(predicate?.({ meta: { angeeModels: ["notes.Tag"] } })).toBe(false);
  });

  test("one upstream change invalidates once however many consumers share it", async () => {
    const { subscribe, sinks } = recordingClient();
    const invalidateQueries = vi.fn();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: "noteChanged", list: "notes", model: "notes.Note" })],
      { queryClient: invalidationClient(invalidateQueries) },
    );
    const callbacks = [vi.fn(), vi.fn(), vi.fn()];
    provider.subscribe({ channel: "resources/notes", types: ["*"], callback: callbacks[0]!, params: { resource: "notes" } });
    provider.subscribe({ channel: "angee/authored/notes.Note", types: ["*"], callback: callbacks[1]!, params: { models: ["notes.Note"] } });
    provider.subscribe({ channel: "angee/authored/notes.Note", types: ["*"], callback: callbacks[2]!, params: { models: ["notes.Note"] } });
    expect(subscribe).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    nthSink(sinks, 0).next({
      data: { noteChanged: { model: "notes.Note", id: "note_1", action: "update" } },
    });
    await vi.advanceTimersByTimeAsync(300);

    expect(invalidateQueries).toHaveBeenCalledTimes(1);
    const predicate = invalidateQueries.mock.calls.find(([filters]) => filters.refetchType === "active")?.[0]?.predicate as (query: { meta: unknown }) => boolean;
    expect(predicate({ meta: { angeeModels: ["notes.Note"] } })).toBe(true);
    for (const callback of callbacks) expect(callback).toHaveBeenCalledTimes(1);
  });

  test("ignores authored models with no change root", () => {
    const subscribe = vi.fn();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [resource({ changes: null })],
    );

    const subscription = provider.subscribe({
      channel: "angee/authored/notes.Note",
      types: ["*"],
      callback: vi.fn(),
      params: { models: ["notes.Note", "unknown.Model"] },
    });
    provider.unsubscribe(subscription);

    expect(subscribe).not.toHaveBeenCalled();
  });

  test("keeps a separate upstream subscription per change root", () => {
    const { subscribe, sinks } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on: vi.fn(() => () => undefined) } as never,
      [
        resource({ changes: "noteChanged" }),
        resource({ changes: "tagChanged", list: "tags", model: "notes.Tag" }),
      ],
    );

    const subNotes = provider.subscribe({
      channel: "resources/notes",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "notes" },
    });
    const subTags = provider.subscribe({
      channel: "resources/tags",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "tags" },
    });

    expect(subscribe).toHaveBeenCalledTimes(2);

    provider.unsubscribe(subNotes);
    expect(nthSink(sinks, 0).dispose).toHaveBeenCalledTimes(1);
    expect(nthSink(sinks, 1).dispose).not.toHaveBeenCalled();

    provider.unsubscribe(subTags);
    expect(nthSink(sinks, 1).dispose).toHaveBeenCalledTimes(1);
  });
});

interface RecordedSink {
  next: (result: { data: unknown }) => void;
  error: (error: unknown) => void;
  dispose: ReturnType<typeof vi.fn>;
}

function recordingClient(): {
  subscribe: ReturnType<typeof vi.fn>;
  sinks: RecordedSink[];
} {
  const sinks: RecordedSink[] = [];
  const subscribe = vi.fn((_payload, sink) => {
    const dispose = vi.fn();
    sinks.push({ next: sink.next, error: sink.error, dispose });
    return dispose;
  });
  return { subscribe, sinks };
}

function nthSink(sinks: readonly RecordedSink[], index: number): RecordedSink {
  const sink = sinks[index];
  if (!sink) throw new Error(`No upstream subscription at index ${index}`);
  return sink;
}

function resource({
  changes,
  list = "notes",
  model = "notes.Note",
}: {
  changes: string | null;
  list?: string;
  model?: string;
}): AngeeLiveResource {
  return {
    schemaName: "console",
    modelLabel: model,
    roots: {
      list,
      changes,
    },
  };
}


test("stale revision transport retains only a valid current revision for explicit overwrite", () => {
  expect(publicGraphQLError({ message: "Stale", extensions: { code: "STALE_REVISION", current_revision: 0, secret: "private" } }))
    .toEqual({ message: "Stale", extensions: { code: "STALE_REVISION", current_revision: 0 } });
  expect(publicGraphQLError({ message: "Stale", extensions: { code: "STALE_REVISION", current_revision: "unsafe" } })?.extensions)
    .toEqual({ code: "STALE_REVISION" });
});

describe("retryableQueryError", () => {
  const answer = (code: string) => ({ message: "The requested record was not found.", extensions: { code } });

  test.each(["VALIDATION", "PERMISSION_DENIED", "UNAUTHENTICATED", "STALE_REVISION"])(
    "a %s answer is not retried, from graphql-request or urql",
    (code) => {
      expect(retryableQueryError({ response: { status: 200, errors: [answer(code)] } })).toBe(false);
      expect(retryableQueryError({ graphQLErrors: [answer(code)] })).toBe(false);
    },
  );

  test("transport failures and masked server errors are retried", () => {
    expect(retryableQueryError(new TypeError("Failed to fetch"))).toBe(true);
    expect(retryableQueryError({ response: { status: 502, errors: [] } })).toBe(true);
    expect(retryableQueryError({ graphQLErrors: [answer("INTERNAL_SERVER_ERROR")] })).toBe(true);
  });
});

test("the shared GraphQL transport resolves its configured endpoint on each request", async () => {
  let endpoint = "https://daemon.example.test/ops/graphql";
  const urls: string[] = [];
  const client = createAngeeGraphQLClient({
    url: () => endpoint,
    auth: (fetch) => fetch,
    fetch: async (input) => { urls.push(String(input)); return jsonResponse({ ping: true }); },
  });
  await client.request("query { ping }");
  endpoint = "https://replacement.example.test/graphql";
  await client.request("query { ping }");
  expect(urls).toEqual(["https://daemon.example.test/ops/graphql", "https://replacement.example.test/graphql"]);
});
