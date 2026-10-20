// @vitest-environment happy-dom
import { useInvalidateAuthoredModels } from "@angee/refine";
import { createRefineTestProviders } from "@angee/refine/testing";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { useTimelinePages } from "./useTimelinePages";

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
    const [header] = useTimelinePages({ model: "notes.Note", id: "nte_7" });
    const [rail] = useTimelinePages({ model: "notes.Note", id: "nte_7", kinds: null, needsMe: false }, [undefined]);
    const invalidate = useInvalidateAuthoredModels();
    return <button onClick={() => invalidate(["notes.Note"])}>
      {header?.data?.timeline.needs_me}:{rail?.data?.timeline.needs_me}
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

test("an unsaved record neither reads the ledger nor subscribes", async () => {
  const custom = vi.fn();
  const subscribe = vi.fn<Subscribe>(() => "timeline-subscription");
  function Read() { useTimelinePages({ model: "notes.Note", id: "" }); return null; }
  await act(async () => root.render(<Provider dataProvider={{ custom }}
    liveProvider={{ subscribe, unsubscribe: vi.fn() }}><Read /></Provider>));
  expect(custom).not.toHaveBeenCalled();
  expect(subscribe).not.toHaveBeenCalled();
});
