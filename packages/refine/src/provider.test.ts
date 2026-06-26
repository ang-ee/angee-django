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
        query: "subscription angee_noteChanged { noteChanged { model id action changedFields changedValues } }",
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
});

function resource({ changes }: { changes: string | null }): AngeeLiveResource {
  return {
    schemaName: "console",
    modelLabel: "notes.Note",
    roots: {
      list: "notes",
      changes,
    },
  };
}
