import { afterEach, describe, expect, test, vi } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import type { AngeeLiveResource } from "./provider";

import {
  ANGEE_HASURA_PROVIDER_OPTIONS,
  createAngeeChangeLiveProvider,
  resolveGraphQLWebSocketEndpoint,
} from "./provider";

describe("Angee Hasura provider defaults", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("pins the stock provider to Angee's Hasura dialect", () => {
    expect(ANGEE_HASURA_PROVIDER_OPTIONS).toEqual({
      idType: "String",
      namingConvention: "hasura-default",
    });
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
        query: "subscription angee_noteChanged { noteChanged { model id action changedFields: changed_fields changedValues: changed_values } }",
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
      { queryClient: { invalidateQueries, cancelQueries: vi.fn(async () => undefined) } },
    );

    provider.subscribe({
      channel: "resources/workflow_decisions",
      types: ["*"],
      callback: vi.fn(),
      params: { resource: "workflow_decisions" },
    });
    nthSink(sinks, 0).next({
      data: {
        decisionChanged: {
          model: "workflows.Decision",
          id: "dec_1",
          action: "update",
        },
      },
    });

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

  test("revalidates native authored data on socket reconnect and removes its listener with the last consumer", async () => {
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
    const listeners = new Set<() => void>();
    const stopListening = vi.fn();
    const on = vi.fn((event: string, listener: () => void) => {
      expect(event).toBe("connected");
      listeners.add(listener);
      return () => { listeners.delete(listener); stopListening(); };
    });
    const { subscribe } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe, on } as never,
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
      expect(on).toHaveBeenCalledTimes(1);
      // The initial connection also closes the gap between HTTP and subscribing.
      listeners.forEach((connected) => connected());
      await vi.waitFor(() => expect(queryFn).toHaveBeenCalledTimes(3));
      rows = ["survivor"];
      listeners.forEach((connected) => connected());
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(["survivor"]));
      expect(client.getQueryState(["message-feed", "inactive"])?.isInvalidated).toBe(true);
      expect(unrelated.queryFn).toHaveBeenCalledTimes(1);
      expect(client.getQueryState(unrelated.queryKey)?.isInvalidated).toBe(false);
      provider.unsubscribe(first);
      expect(listeners.size).toBe(1);
      provider.unsubscribe(second);
      expect(listeners.size).toBe(0);
      expect(stopListening).toHaveBeenCalledTimes(1);
      const next = subscription();
      expect(on).toHaveBeenCalledTimes(2);
      provider.unsubscribe(next);
      expect(listeners.size).toBe(0);
    } finally {
      provider.unsubscribe(first);
      provider.unsubscribe(second);
      unsubscribeObserver();
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
          "subscription angee_noteChanged { noteChanged { model id action changedFields: changed_fields changedValues: changed_values } }",
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
      { queryClient: { invalidateQueries, cancelQueries: vi.fn(async () => undefined) } },
    );

    provider.subscribe({
      channel: "angee/authored/notes.Note",
      types: ["*"],
      callback: vi.fn(),
      params: { models: ["notes.Note"] },
    });
    nthSink(sinks, 0).next({
      data: { noteChanged: { model: "notes.Note", id: "note_1", action: "update" } },
    });

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
