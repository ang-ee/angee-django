// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { authoredQueryReadsAnyModel, createAngeeChangeLiveProvider } from "@angee/refine";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("@angee/app", async (original) => ({
  ...(await original<typeof import("@angee/app")>()),
  useAuth: () => ({ user: { id: "actor" } }),
}));

import { NexusTimeline, NexusTimelineRevalidate } from "./documents";
import { TimelinePane } from "./TimelinePane";

const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients.length = 0; });

const row = (id: number) => ({ id: `message-${id}`, feed_order_key: `v1:000${id}`, preview: `Message ${id}`, sender: null, thread: null });

function fixture() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  let refreshed = false;
  let denied = false;
  let missing = false;
  let emptied = false;
  const custom = vi.fn(async ({ meta }: { meta: { gqlQuery: unknown; gqlVariables: Record<string, unknown> } }) => {
    if (denied) throw new Error("Unreadable scope");
    if (missing) return { data: {} };
    const variables = meta.gqlVariables;
    const kind = variables.circle ? "circle" : "party";
    if (meta.gqlQuery === NexusTimelineRevalidate) {
      const ids = variables.ids as string[];
      return { data: { [`${kind}_message_feed_revalidate`]: {
        messages: ids.filter((id) => !emptied && id !== "message-2").map((id) => row(Number(id.slice(-1)))),
        absent_ids: ids.filter((id) => emptied || id === "message-2"),
      } } };
    }
    expect(meta.gqlQuery).toBe(NexusTimeline);
    const messages = variables.beforeCursor ? [row(1)] : emptied ? [] : refreshed ? [row(4), row(3)] : [row(3), row(2)];
    return { data: { [`${kind}_message_feed`]: {
      messages, count: 4, older_cursor: messages.length ? `opaque-${messages.at(-1)!.id}` : null,
      has_older: !variables.beforeCursor,
      has_more_in_window: false,
      has_older_than_through: !variables.beforeCursor,
    } } };
  });
  const provider = { getApiUrl: () => "test://timeline", getList: vi.fn(), getOne: vi.fn(), create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(), custom } as unknown as DataProvider;
  const onError = vi.fn(async () => ({}));
  const roots = [
    ["messaging.Message", "messageChanged"], ["messaging.Thread", "threadChanged"],
    ["parties.Handle", "handleChanged"], ["parties.Party", "partyChanged"],
    ["parties.Circle", "circleChanged"], ["parties.CircleMember", "circleMemberChanged"],
  ] as const;
  const sinks = new Map<string, { next(value: unknown): void }>();
  const liveProvider = createAngeeChangeLiveProvider({
    subscribe: ({ query }: { query: string }, sink: { next(value: unknown): void }) => {
      const root = roots.find(([, field]) => query.includes(`angee_${field} `))![1];
      sinks.set(root, sink);
      return () => { sinks.delete(root); };
    }, on: () => () => undefined,
  } as never, roots.map(([modelLabel, changes]) => ({ schemaName: "console", modelLabel, roots: { changes } })), { queryClient: client });
  function Providers({ children }: { children: ReactNode }) {
    return <Refine dataProvider={provider} liveProvider={liveProvider}
      authProvider={{ login: async () => ({ success: true }), logout: async () => ({ success: true }), check: async () => ({ authenticated: true }), onError }}
      options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>{children}</Refine>;
  }
  return { client, custom, wrapper: Providers, refresh: () => { refreshed = true; }, deny: () => { denied = true; }, missing: () => { missing = true; }, empty: () => { emptied = true; },
    emit: (model: string) => {
      const root = roots.find(([label]) => label === model)![1];
      expect(sinks.has(root)).toBe(true);
      sinks.get(root)!.next({ data: { [root]: { model, id: "changed", action: "update" } } });
    } };
}

test.each(["party", "circle"] as const)("%s timeline uses native windows, revalidation and model-interest refresh", async (kind) => {
  const feed = fixture();
  render(kind === "circle" ? <TimelinePane circleId="root" /> : <TimelinePane partyId="root" />, { wrapper: feed.wrapper });
  await screen.findByText("Message 2");
  expect(feed.custom.mock.calls[0]?.[0].meta.gqlVariables).toEqual({
    partyId: "root", circleId: "root", circle: kind === "circle", search: "", beforeCursor: null, throughCursor: null, limit: 30,
  });
  fireEvent.click(screen.getByRole("button"));
  await screen.findByText("Message 1");
  expect(feed.custom.mock.calls[1]?.[0].meta.gqlVariables.beforeCursor).toBe("opaque-message-2");
  feed.refresh();
  await act(async () => { await feed.client.invalidateQueries({ predicate: (query) => authoredQueryReadsAnyModel(query.meta, [kind === "circle" ? "parties.CircleMember" : "parties.Handle"]) }); });
  await screen.findByText("Message 4");
  expect(screen.queryByText("Message 2")).toBeNull();
  expect(screen.getByText("Message 1")).toBeTruthy();
  const revalidation = feed.custom.mock.calls.filter(([request]) => request.meta.gqlQuery === NexusTimelineRevalidate);
  expect(revalidation.map(([request]) => request.meta.gqlVariables.ids)).toEqual([["message-3", "message-2"], ["message-1"]]);
  const queries = feed.client.getQueryCache().findAll({ queryKey: ["angee", "authored"] });
  expect(queries).toHaveLength(1);
  expect(queries[0]?.meta?.angeeModels).toEqual(kind === "circle"
    ? ["messaging.Message", "messaging.Thread", "parties.Circle", "parties.CircleMember", "parties.Handle", "parties.Party"]
    : ["messaging.Message", "messaging.Thread", "parties.Handle", "parties.Party"]);
});

test.each(["parties.Handle", "parties.Party", "messaging.Thread", "parties.Circle"])("actual %s changes revalidate the composed circle timeline", async (model) => {
  const feed = fixture();
  render(<TimelinePane circleId="root" />, { wrapper: feed.wrapper });
  await screen.findByText("Message 2"); feed.refresh();
  act(() => feed.emit(model));
  await screen.findByText("Message 4");
  expect(screen.queryByText("Message 2")).toBeNull();
  expect(feed.custom.mock.calls.some(([request]) => request.meta.gqlQuery === NexusTimelineRevalidate)).toBe(true);
});

test("actual native refetch failure hides old rendered messages", async () => {
  const feed = fixture();
  render(<TimelinePane partyId="root" />, { wrapper: feed.wrapper });
  await screen.findByText("Message 2"); feed.deny();
  await act(async () => { await feed.client.invalidateQueries({ queryKey: ["angee", "authored"] }); });
  await screen.findByText("Unreadable scope");
  expect(screen.queryByText("Message 2")).toBeNull();
  expect(feed.client.getQueryCache().findAll({ queryKey: ["angee", "authored"] })[0]?.state.data).toBeDefined();
});

test("a missing selected GraphQL root is an error, not an empty authorized timeline", async () => {
  const feed = fixture(); feed.missing();
  render(<TimelinePane circleId="root" />, { wrapper: feed.wrapper });
  await waitFor(() => expect(screen.getByText("Timeline window returned no scope data.")).toBeTruthy());
});

test("an empty refreshed timeline still loads messages below its retained cuts", async () => {
  const feed = fixture();
  render(<TimelinePane partyId="root" />, { wrapper: feed.wrapper });
  await screen.findByText("Message 2"); feed.empty();
  await act(async () => { await feed.client.invalidateQueries({ queryKey: ["angee", "authored"] }); });
  await waitFor(() => expect(screen.queryByText("Message 2")).toBeNull());
  fireEvent.click(screen.getByRole("button"));
  await screen.findByText("Message 1");
  expect(feed.custom.mock.calls.at(-1)?.[0].meta.gqlVariables.beforeCursor).toBe("opaque-message-2");
});
