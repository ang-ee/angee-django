import { afterEach, describe, expect, test, vi } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { parse } from "graphql";
import type { AngeeLiveResource, GraphQLWsClient } from "./provider";

import {
  ANGEE_HASURA_PROVIDER_OPTIONS,
  boundedGraphQLTransportError,
  publicGraphQLError,
  createAngeeGraphQLClient,
  createAngeeHasuraDataProvider,
  createAngeeChangeLiveProvider,
  resolveGraphQLWebSocketEndpoint,
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
        create: { root: "insert_notes_one", inputType: "notes_insert_input", arguments: [{ name: "client_creation_key", type: "String!" }] },
        update: { root: "update_notes_by_pk", inputType: "notes_set_input", arguments: [{ name: "expected_revision", type: "Int!" }] },
      } },
      fetch: async (_input, init) => {
        requests.push(JSON.parse(String(init?.body)));
        return jsonResponse({ insert_notes_one: { id: "note-1" }, update_notes_by_pk: { id: "note-1", revision: 4 } });
      },
    });
    const created = await provider.create({ resource: "notes", variables: { title: "New" },
      meta: { fields: ["id"], gqlVariables: { client_creation_key: "session-key", expected_revision: 99 } } });
    expect(created.data.id).toBe("note-1");
    const updated = await provider.update({ resource: "notes", id: "note-1", variables: { title: "Changed" },
      meta: { fields: ["id", "revision"], gqlVariables: { expected_revision: 3 } } });
    expect(updated.data.revision).toBe(4);
    expect(requests[0]?.variables).toMatchObject({ object: { title: "New" }, client_creation_key: "session-key" });
    expect(requests[0]?.query).toContain("$client_creation_key: String!");
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
    expect(invalidateQueries).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);

    await vi.waitFor(() => expect(invalidateQueries).toHaveBeenCalledWith({
      predicate: expect.any(Function),
      type: "all",
      refetchType: "active",
    }));
    const predicate = invalidateQueries.mock.calls[0]?.[0]?.predicate as
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

  test("revalidates retained authored data on socket reconnect and removes its listener with the last consumer", async () => {
    const client = new QueryClient();
    let rows = ["survivor", "revoked"];
    const queryFn = vi.fn(async () => rows);
    const noteQuery = {
      queryKey: ["message-feed", "current"],
      queryFn,
      meta: { angeeModels: ["notes.Note"] },
      staleTime: Infinity,
    };
    const unrelated = {
      queryKey: ["unrelated"],
      queryFn: vi.fn(async () => ["tag"]),
      meta: { angeeModels: ["notes.Tag"] },
      staleTime: Infinity,
    };
    await client.fetchQuery(noteQuery);
    await client.fetchQuery(unrelated);
    await client.fetchQuery({ ...noteQuery, queryKey: ["message-feed", "inactive"] });
    const observer = new QueryObserver(client, noteQuery);
    const unsubscribeObserver = observer.subscribe(() => undefined);
    const socket = connectingClient();
    const provider = createAngeeChangeLiveProvider(
      socket.client,
      [resource({ changes: "noteChanged" }), resource({ changes: "tagChanged", list: "tags", model: "notes.Tag" })],
      { queryClient: client },
    );
    const subscription = () => provider.subscribe({
      channel: "angee/authored/notes.Note", types: ["*"], callback: vi.fn(),
      params: { models: ["notes.Note"] },
    });
    const first = subscription();
    const second = subscription();
    try {
      expect(socket.client.on).toHaveBeenCalledTimes(1);
      socket.connect(false);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(queryFn).toHaveBeenCalledTimes(2);
      rows = ["survivor"];
      socket.connect(true);
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["survivor"]));
      expect(queryFn).toHaveBeenCalledTimes(3);
      expect(client.getQueryState(["message-feed", "inactive"])?.isInvalidated).toBe(true);
      expect(unrelated.queryFn).toHaveBeenCalledTimes(1);
      expect(client.getQueryState(unrelated.queryKey)?.isInvalidated).toBe(false);
      provider.unsubscribe(first);
      provider.unsubscribe(second);
      expect(socket.connect(false)).toBe(0);
      const next = subscription();
      expect(socket.client.on).toHaveBeenCalledTimes(2);
      rows = ["latest"];
      socket.connect(false);
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["latest"]));
      expect(queryFn).toHaveBeenCalledTimes(4);
      provider.unsubscribe(next);
      expect(socket.connect(false)).toBe(0);
    } finally {
      provider.unsubscribe(first);
      provider.unsubscribe(second);
      unsubscribeObserver();
      client.clear();
    }
  });

  test("keeps first loads running across first and idle-reopened connections", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let resolveNote!: (rows: string[]) => void;
    let resolveTag!: (rows: string[]) => void;
    const noteRequest = new Promise<string[]>((resolve) => { resolveNote = resolve; });
    const tagRequest = new Promise<string[]>((resolve) => { resolveTag = resolve; });
    const noteQuery = vi.fn(({ signal }: { signal: AbortSignal }) => {
      expect(signal.aborted).toBe(false);
      return noteRequest;
    });
    const tagQuery = vi.fn(({ signal }: { signal: AbortSignal }) => {
      expect(signal.aborted).toBe(false);
      return tagRequest;
    });
    const notes = new QueryObserver(client, {
      queryKey: ["notes"], queryFn: noteQuery, meta: { angeeModels: ["notes.Note"] },
    });
    const tags = new QueryObserver(client, {
      queryKey: ["tags"], queryFn: tagQuery, meta: { angeeModels: ["notes.Tag"] },
    });
    const stopNotes = notes.subscribe(() => undefined);
    const cancelQueries = vi.spyOn(client, "cancelQueries");
    const socket = connectingClient();
    const provider = createAngeeChangeLiveProvider(
      socket.client,
      [resource({ changes: "noteChanged" }), resource({ changes: "tagChanged", list: "tags", model: "notes.Tag" })],
      { queryClient: client },
    );
    const first = provider.subscribe({
      channel: "notes", types: ["*"], callback: vi.fn(), params: { resource: "notes" },
    });
    try {
      socket.connect(false);
      socket.connect(true);
      expect(noteQuery).toHaveBeenCalledOnce();
      expect(cancelQueries).not.toHaveBeenCalled();
      provider.unsubscribe(first);
      const stopTags = tags.subscribe(() => undefined);
      const second = provider.subscribe({
        channel: "tags", types: ["*"], callback: vi.fn(), params: { resource: "tags" },
      });
      try {
        // graphql-ws reports an idle-close reopening as a non-retry connection.
        resolveTag(["tag"]);
        await vi.waitFor(() => expect(tags.getCurrentResult().data).toEqual(["tag"]));
        socket.connect(false);
        expect(tagQuery).toHaveBeenCalledOnce();
        expect(cancelQueries).not.toHaveBeenCalled();
        resolveNote(["note"]);
        await vi.waitFor(() => expect(notes.getCurrentResult().data).toEqual(["note"]));
      } finally {
        provider.unsubscribe(second);
        stopTags();
      }
    } finally {
      resolveNote([]);
      resolveTag([]);
      provider.unsubscribe(first);
      stopNotes();
      client.clear();
    }
  });

  test.each(["first", "idle"])("keeps a cached query's mount refetch across a %s socket connection", async (connection) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const queryKey = ["notes", "revisited"];
    client.setQueryData(queryKey, ["cached"], { updatedAt: Date.now() - 31_000 });
    let finish!: (rows: string[]) => void;
    const request = new Promise<string[]>((resolve) => { finish = resolve; });
    let signal!: AbortSignal;
    const queryFn = vi.fn(({ signal: requestSignal }: { signal: AbortSignal }) => {
      signal = requestSignal;
      return request;
    });
    const observer = new QueryObserver(client, {
      queryKey, queryFn, staleTime: 30_000, meta: { angeeModels: ["notes.Note"] },
    });
    const socket = connectingClient();
    const provider = createAngeeChangeLiveProvider(
      socket.client,
      [resource({ changes: "noteChanged" })],
      { queryClient: client },
    );
    const subscription = () => provider.subscribe({
      channel: "notes", types: ["*"], callback: vi.fn(), params: { resource: "notes" },
    });
    if (connection === "idle") {
      const first = subscription();
      socket.connect(false);
      provider.unsubscribe(first);
    }
    const stopObserver = observer.subscribe(() => undefined);
    const second = subscription();
    try {
      expect(queryFn).toHaveBeenCalledOnce();
      socket.connect(false);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(queryFn).toHaveBeenCalledOnce();
      expect(signal.aborted).toBe(false);
      finish(["fresh"]);
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["fresh"]));
    } finally {
      finish([]);
      provider.unsubscribe(second);
      stopObserver();
      client.clear();
    }
  });

  test("restarts a data-holding refetch on a genuine socket retry", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const queryKey = ["notes", "retry"];
    client.setQueryData(queryKey, ["cached"]);
    const attempts: { signal: AbortSignal; resolve: (rows: string[]) => void }[] = [];
    const queryFn = vi.fn(({ signal }: { signal: AbortSignal }) => new Promise<string[]>((resolve) => {
      attempts.push({ signal, resolve });
    }));
    const observer = new QueryObserver(client, {
      queryKey, queryFn, staleTime: Infinity, meta: { angeeModels: ["notes.Note"] },
    });
    const stopObserver = observer.subscribe(() => undefined);
    const socket = connectingClient();
    const provider = createAngeeChangeLiveProvider(
      socket.client,
      [resource({ changes: "noteChanged" })],
      { queryClient: client },
    );
    const subscription = provider.subscribe({
      channel: "notes", types: ["*"], callback: vi.fn(), params: { resource: "notes" },
    });
    try {
      socket.connect(false);
      void client.refetchQueries({ queryKey, exact: true });
      expect(queryFn).toHaveBeenCalledOnce();
      socket.connect(true);
      await vi.waitFor(() => expect(queryFn).toHaveBeenCalledTimes(2));
      expect(attempts[0]?.signal.aborted).toBe(true);
      attempts[0]?.resolve(["obsolete"]);
      expect(observer.getCurrentResult().data).toEqual(["cached"]);
      attempts[1]?.resolve(["fresh"]);
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["fresh"]));
    } finally {
      attempts.forEach((attempt) => attempt.resolve([]));
      provider.unsubscribe(subscription);
      stopObserver();
      client.clear();
    }
  });

  test("catches up reads loaded while subscriptions were disabled", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let rows = ["before-enable"];
    const queryFn = vi.fn(async () => rows);
    const observer = new QueryObserver(client, {
      queryKey: ["notes"], queryFn, staleTime: Infinity, meta: { angeeModels: ["notes.Note"] },
    });
    const socket = connectingClient();
    const provider = createAngeeChangeLiveProvider(
      socket.client,
      [resource({ changes: "noteChanged" })],
      { queryClient: client },
    );
    const subscription = () => provider.subscribe({
      channel: "notes", types: ["*"], callback: vi.fn(), params: { resource: "notes" },
    });
    const first = subscription();
    socket.connect(false);
    provider.unsubscribe(first);
    provider.setEnabled(false);
    const second = subscription();
    const stopObserver = observer.subscribe(() => undefined);
    try {
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["before-enable"]));
      rows = ["after-enable"];
      provider.setEnabled(true);
      socket.connect(false);
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["after-enable"]));
      expect(queryFn).toHaveBeenCalledTimes(2);
    } finally {
      provider.unsubscribe(second);
      stopObserver();
      client.clear();
    }
  });

  test("clears an unused enable catch-up before a later first connection", async () => {
    const client = new QueryClient();
    const queryFn = vi.fn(async () => ["cached"]);
    const query = {
      queryKey: ["notes", "enable"], queryFn, staleTime: Infinity,
      meta: { angeeModels: ["notes.Note"] },
    };
    await client.fetchQuery(query);
    const observer = new QueryObserver(client, query);
    const stopObserver = observer.subscribe(() => undefined);
    const socket = connectingClient();
    const provider = createAngeeChangeLiveProvider(
      socket.client, [resource({ changes: "noteChanged" })], { queryClient: client },
    );
    const subscribe = () => provider.subscribe({
      channel: "notes", types: ["*"], callback: vi.fn(), params: { resource: "notes" },
    });
    provider.setEnabled(false);
    const first = subscribe();
    provider.setEnabled(true);
    provider.unsubscribe(first);
    const second = subscribe();
    try {
      socket.connect(false);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(queryFn).toHaveBeenCalledOnce();
    } finally {
      provider.unsubscribe(second);
      stopObserver();
      client.clear();
    }
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
    const predicate = invalidateQueries.mock.calls[0]?.[0]?.predicate as
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
    const predicate = invalidateQueries.mock.calls[0]?.[0]?.predicate as (query: { meta: unknown }) => boolean;
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

function connectingClient(): {
  client: Pick<GraphQLWsClient, "subscribe" | "on">;
  connect: (wasRetry: boolean) => number;
} {
  const listeners = new Set<(socket: unknown, payload: unknown, wasRetry: boolean) => void>();
  const on: GraphQLWsClient["on"] = (event, listener) => {
    if (event !== "connected") throw new Error(`Unexpected socket event: ${event}`);
    const connected = listener as (socket: unknown, payload: unknown, wasRetry: boolean) => void;
    listeners.add(connected);
    return () => { listeners.delete(connected); };
  };
  return {
    client: { subscribe: () => () => undefined, on: vi.fn(on) },
    connect(wasRetry) {
      listeners.forEach((connected) => connected(null, undefined, wasRetry));
      return listeners.size;
    },
  };
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
