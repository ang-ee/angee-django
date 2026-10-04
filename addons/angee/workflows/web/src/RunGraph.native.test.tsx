// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { createAngeeChangeLiveProvider } from "@angee/refine";
import { createRefineTestProviders } from "@angee/refine/testing";
import { RUN_GRAPH_INSPECTOR_TAB } from "./RunGraph";
import { RunGraphStory } from "./RunGraph.stories";
import type { RunRequest } from "./RunsPage.stories";
import { RUN_MODEL } from "./documents.console";
import { mappedRunGraphFixture } from "./testing";

beforeAll(() => {
  class ResizeObserverStub { observe(): void {} unobserve(): void {} disconnect(): void {} }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Element.prototype.getAnimations ??= () => [];
});
const { createClient, clearClients } = createRefineTestProviders();
afterEach(() => { cleanup(); clearClients(); });

test.each(["click", "Enter"])("%s selects the URL node and expands the native inspector", async (activation) => {
  render(<RunGraphStory retained />);
  fireEvent.click(await screen.findByRole("button", { name: "Collapse inspector" }));
  const node = await screen.findByTestId("rf__node-inspect");
  if (activation === "click") fireEvent.click(node);
  else fireEvent.keyDown(node, { key: "Enter" });
  await waitFor(() => expect(screen.getByTestId("graph-node-selection").textContent).toBe("inspect"));
  expect(screen.getByTestId("graph-pane-state").textContent).toBe(`false:${RUN_GRAPH_INSPECTOR_TAB}`);
  expect(await screen.findByRole("button", { name: "Open Inspect source" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "Step Run" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open Inspect source" }));
  expect(await screen.findByRole("dialog", { name: "Step Run" })).toBeTruthy();
});

test("an inactive graph sends no request and publishes no inspector", async () => {
  const requests = vi.fn();
  const client = createClient();
  render(<RunGraphStory active={false} retained onRequest={requests} queryClient={client} />);
  await act(async () => {});
  expect(requests).not.toHaveBeenCalled();
  await waitFor(() => {
    const query = client.getQueryCache().findAll().find((query) => query.queryKey[1] === "authored");
    expect(query?.state).toMatchObject({ fetchStatus: "idle", dataUpdateCount: 0 });
  });
  expect(screen.getByTestId("shell-chatter").getAttribute("data-tab-ids")).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Show graph" }));
  await screen.findByTestId("rf__node-inspect");
  expect(requests.mock.calls.filter(([request]) => request.query.includes("WorkflowRunGraph"))).toHaveLength(1);
});

test("retained tabs preserve the canvas and selection while withdrawing their inspector", async () => {
  render(<RunGraphStory retained />);
  const canvas = await screen.findByTestId("run-graph-canvas");
  fireEvent.click(await screen.findByTestId("rf__node-finish"));
  await waitFor(() => expect(screen.getByTestId("graph-node-selection").textContent).toBe("finish"));
  fireEvent.click(screen.getByRole("button", { name: "Hide graph" }));
  await waitFor(() => expect(screen.getByTestId("shell-chatter").getAttribute("data-tab-ids")).toBe(""));
  fireEvent.click(screen.getByRole("button", { name: "Show graph" }));
  await waitFor(() => expect(screen.getByTestId(`tab-${RUN_GRAPH_INSPECTOR_TAB}`)).toBeTruthy());
  expect(screen.getByTestId("run-graph-canvas")).toBe(canvas);
  expect(screen.getByTestId("graph-node-selection").textContent).toBe("finish");
});

test("a map inspector shows complete progress and filters the node and its items", async () => {
  const requests: RunRequest[] = [];
  render(<RunGraphStory graph={mappedRunGraphFixture()} onRequest={(request) => requests.push(request)} />);
  const node = await screen.findByTestId("rf__node-reviews");
  expect(node.textContent).toContain("Waiting");
  expect(node.textContent).toContain("119/122");
  fireEvent.click(await screen.findByTestId("rf__node-reviews"));
  await waitFor(() => expect(requests.some(({ query }) => /\bsteprun\s*\(/.test(query))).toBe(true));
  // `_and` is commutative; the shared filter owner decides clause order.
  const where = requests.find(({ query }) => /\bsteprun\s*\(/.test(query))?.variables.where as { _and: unknown[] };
  expect(where._and).toHaveLength(2);
  expect(where._and).toEqual(expect.arrayContaining([
    { run: { _eq: "wfr_review" } }, { node_key: { _in: ["reviews", "reviews.body"] } },
  ]));
  expect(screen.queryByRole("dialog", { name: "Step Run" })).toBeNull();
  const query = requests.find(({ query }) => query.includes("WorkflowRunGraph"))!.query;
  expect(query).not.toMatch(/\b(input|output|state|stacktrace)\b/);
});

test("committed run changes refetch once while preserving the canvas and node URL", async () => {
  const client = createClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  let change: ((id: string) => void) | undefined;
  const socket: Parameters<typeof createAngeeChangeLiveProvider>[0] = {
    subscribe: (_request, sink) => {
      change = (id) => {
        const event = { data: { workflowRunChanged: { model: RUN_MODEL, id, action: "update" } } };
        sink.next(event as Parameters<typeof sink.next>[0]);
      };
      return () => { change = undefined; };
    },
    on: () => () => undefined,
  };
  const liveProvider = createAngeeChangeLiveProvider(socket, [{ schemaName: "console", modelLabel: RUN_MODEL,
    roots: { list: "workflowrun", changes: "workflowRunChanged" } }], { queryClient: client });
  const requests: RunRequest[] = [];
  render(<RunGraphStory retained initialEntry="/workflows/runs/wfr_review?node=inspect"
    liveProvider={liveProvider} queryClient={client} onRequest={(request) => requests.push(request)} />);
  const canvas = await screen.findByTestId("run-graph-canvas");
  await waitFor(() => expect(change).toBeTypeOf("function"));
  const reads = () => requests.filter(({ query }) => query.includes("WorkflowRunGraph")).length;
  expect(reads()).toBe(1);
  await act(async () => { change!("wfr_review"); });
  await waitFor(() => expect(reads()).toBe(2));
  await waitFor(() => expect(client.getQueryCache().findAll().every((query) => query.state.fetchStatus === "idle")).toBe(true));
  expect(reads()).toBe(2);
  expect(screen.getByTestId("run-graph-canvas")).toBe(canvas);
  expect(screen.getByTestId("graph-node-selection").textContent).toBe("inspect");
  expect(screen.getByTestId("rf__node-inspect").className).toContain("selected");
});

test("a node deep link selects the node and opens its inspector without a modal", async () => {
  render(<RunGraphStory retained initialEntry="/workflows/runs/wfr_review?node=inspect" />);
  const node = await screen.findByTestId("rf__node-inspect");
  await waitFor(() => expect(node.className).toContain("selected"));
  expect(screen.getByTestId("graph-pane-state").textContent).toBe(`false:${RUN_GRAPH_INSPECTOR_TAB}`);
  expect(await screen.findByRole("button", { name: "Open Inspect source" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "Step Run" })).toBeNull();
});

test("an unknown node deep link opens the empty inspector", async () => {
  render(<RunGraphStory retained initialEntry="/workflows/runs/wfr_review?node=removed" />);
  await screen.findByTestId("run-graph-canvas");
  await waitFor(() => expect(screen.getByTestId("graph-pane-state").textContent).toBe(`false:${RUN_GRAPH_INSPECTOR_TAB}`));
  expect(screen.getByText("Select a node to inspect its progress and retained evidence.")).toBeTruthy();
  expect(screen.getByTestId("rf__node-inspect").className).not.toContain("selected");
  expect(screen.getByTestId("graph-node-selection").textContent).toBe("removed");
});
