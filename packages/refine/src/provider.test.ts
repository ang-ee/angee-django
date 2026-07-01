import { describe, expect, test, vi } from "vitest";
import type { AngeeLiveResource } from "./provider";

import {
  ANGEE_HASURA_PROVIDER_OPTIONS,
  createAngeeChangeLiveProvider,
  resolveGraphQLWebSocketEndpoint,
} from "./provider";

describe("Angee Hasura provider defaults", () => {
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
      { subscribe } as never,
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

  test("skips resources without change roots", () => {
    const subscribe = vi.fn();
    const provider = createAngeeChangeLiveProvider(
      { subscribe } as never,
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

  test("shares one upstream subscription across consumers for the same resource", () => {
    const { subscribe, sinks } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe } as never,
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
      { subscribe } as never,
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

  test("keeps a separate upstream subscription per change root", () => {
    const { subscribe, sinks } = recordingClient();
    const provider = createAngeeChangeLiveProvider(
      { subscribe } as never,
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
  dispose: ReturnType<typeof vi.fn>;
}

function recordingClient(): {
  subscribe: ReturnType<typeof vi.fn>;
  sinks: RecordedSink[];
} {
  const sinks: RecordedSink[] = [];
  const subscribe = vi.fn((_payload, sink) => {
    const dispose = vi.fn();
    sinks.push({ next: sink.next, dispose });
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
