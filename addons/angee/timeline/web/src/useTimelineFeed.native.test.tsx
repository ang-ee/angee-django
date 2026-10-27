// @vitest-environment happy-dom
import { useInvalidateAuthoredModels } from "@angee/refine";
import { createRefineTestProviders } from "@angee/refine/testing";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { useTimelineFeed } from "./useTimelineFeed";

vi.mock("@angee/app", () => ({ useAuth: () => ({ user: { id: "reader" } }) }));

const { Provider, createClient, clearClients } = createRefineTestProviders();
type Subscribe = NonNullable<Parameters<typeof Provider>[0]["liveProvider"]>["subscribe"];
let root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  root = createRoot(document.body.appendChild(document.createElement("div")));
});
afterEach(async () => {
  await act(async () => root.unmount());
  clearClients(); document.body.replaceChildren(); vi.unstubAllGlobals();
});

test("header and rail share a read and subscribe to the exact record", async () => {
  let needsMe = 1;
  const custom = vi.fn(async () => ({ data: { timeline: {
    entries: [], kinds: [{ id: "decisions.decision", count: 1 }],
    needs_me: needsMe, has_more: false, next_before: null,
  } } }));
  const subscribe = vi.fn<Subscribe>(() => "timeline-subscription");
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  function Read() {
    const header = useTimelineFeed({ model: "notes.Note", id: "nte_7" });
    const rail = useTimelineFeed({ model: "notes.Note", id: "nte_7", kinds: null, needsMe: false });
    const invalidate = useInvalidateAuthoredModels();
    return <button onClick={() => invalidate(["notes.Note"])}>
      {header.data?.pages[0]?.metadata?.needsMe}:{rail.data?.pages[0]?.metadata?.needsMe}
    </button>;
  }
  await act(async () => root.render(<Provider queryClient={client} dataProvider={{ custom }}
    liveProvider={{ subscribe, unsubscribe: vi.fn() }}><Read /></Provider>));
  await vi.waitFor(() => expect(document.querySelector("button")?.textContent).toBe("1:1"));
  expect(subscribe).toHaveBeenCalledWith(expect.objectContaining({ params: { models: ["notes.Note"] } }));
  expect(client.getQueryCache().findAll()[0]?.meta).toMatchObject({
    angeeRecords: [{ model: "notes.Note", id: "nte_7" }],
  });
  expect(custom).toHaveBeenCalledTimes(1);
  needsMe = 0;
  await act(async () => document.querySelector("button")?.click());
  await vi.waitFor(() => expect(document.querySelector("button")?.textContent).toBe("0:0"));
  expect(custom).toHaveBeenCalledTimes(2);
});

test("an unsaved record neither reads the timeline nor subscribes", async () => {
  const custom = vi.fn();
  const subscribe = vi.fn<Subscribe>(() => "timeline-subscription");
  function Read() { useTimelineFeed({ model: "notes.Note", id: "" }); return null; }
  await act(async () => root.render(<Provider dataProvider={{ custom }}
    liveProvider={{ subscribe, unsubscribe: vi.fn() }}><Read /></Provider>));
  expect(custom).not.toHaveBeenCalled();
  expect(subscribe).not.toHaveBeenCalled();
});

test("refresh discovers older pages from the new head instead of retaining obsolete cursors", async () => {
  let keys = [5, 4, 3, 2, 1];
  const custom = vi.fn(async ({ meta }: { meta?: { gqlVariables?: Record<string, unknown> } }) => {
    const before = meta?.gqlVariables?.before;
    const limit = Number(meta?.gqlVariables?.limit ?? 2);
    const remaining = keys.filter(key => before == null || key < Number(before));
    const selected = remaining.slice(0, limit);
    const more = remaining.length > limit;
    return { data: { timeline: {
      entries: selected.map(key => ({ key: String(key) })),
      kinds: [], needs_me: 0, has_more: more,
      next_before: more ? String(selected.at(-1)) : null,
    } } };
  });
  function Read() {
    const feed = useTimelineFeed({ model: "notes.Note", id: "nte_7", limit: 2 });
    return <><output>{feed.data?.pages.flatMap(page => page.rows).map(row => row.key).join(",")}</output>
      <button id="older" onClick={() => void feed.fetchNextPage()}>Older</button>
      <button id="refresh" onClick={() => void feed.refetch()}>Refresh</button></>;
  }
  await act(async () => root.render(<Provider dataProvider={{ custom }}><Read /></Provider>));
  await vi.waitFor(() => expect(document.querySelector("output")?.textContent).toBe("5,4"));
  await act(async () => document.querySelector<HTMLButtonElement>("#older")?.click());
  await vi.waitFor(() => expect(document.querySelector("output")?.textContent).toBe("5,4,3,2"));
  keys = [7, 6, ...keys];
  await act(async () => document.querySelector<HTMLButtonElement>("#refresh")?.click());
  await vi.waitFor(() => expect(document.querySelector("output")?.textContent).toBe("7,6,5,4"));
  await act(async () => document.querySelector<HTMLButtonElement>("#older")?.click());
  await vi.waitFor(() => expect(document.querySelector("output")?.textContent).toBe("7,6,5,4,3,2"));
});
