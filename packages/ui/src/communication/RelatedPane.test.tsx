// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const query = vi.hoisted(() => vi.fn());
vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: query,
}));

import { AppRuntimeProvider, containersFromChildren, type ChatterViewContext, type RelatedGroupContent, type RelatedGroupLabel } from "../runtime";
import { Popover } from "../ui/popover";
import { CHATTER_CONTAINERS } from "./chatter-containers";
import { PaneLabelPopover, PaneRow, PaneRows } from "./pane-list";
import { RelatedPane, RelatedCountProbe } from "./RelatedPane";

afterEach(() => { cleanup(); query.mockReset(); });

const groupCounts = (counts: Record<string, number>) => ({
  data: { related_counts: Object.entries(counts).map(([group, count]) => ({ group, count })) },
});

const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record", recordEdges: ["example.Edge"] },
  view: { kind: "record", type: "example/record", sqid: "rec_1" },
};

function Example({ content, probe = false }: { content: RelatedGroupContent; probe?: boolean }) {
  const containers = containersFromChildren(CHATTER_CONTAINERS, { "related#groups": { example: { content } } });
  return <AppRuntimeProvider runtime={{ containers }}>
    {probe ? <Count /> : <RelatedPane context={context} />}
  </AppRuntimeProvider>;
}

function Count() {
  const [count, setCount] = React.useState<number>();
  const publish = React.useCallback((_: string, next: number | undefined) => setCount(next), []);
  return <><RelatedCountProbe id="related" context={context} onCount={publish} /><span>Related {count}</span></>;
}

test("one read counts every group declaring a related count and the badge sums them", () => {
  query.mockReturnValue(groupCounts({ "example.files": 2, "example.pages": 3 }));
  const containers = containersFromChildren(CHATTER_CONTAINERS, { "related#groups": {
    files: { content: { label: "Files", count: { id: "example.files", models: ["example.File"] }, render: () => null } },
    pages: { content: { label: "Pages", count: { id: "example.pages", models: ["example.Page"] }, render: () => null } },
  } });
  const onCount = vi.fn();
  render(<AppRuntimeProvider runtime={{ containers }}>
    <RelatedCountProbe id="related" context={context} onCount={onCount} />
  </AppRuntimeProvider>);
  expect(onCount).toHaveBeenLastCalledWith("related", 5);
  expect(query).toHaveBeenCalled();
  for (const [, variables, options] of query.mock.calls) {
    expect(variables).toEqual({ model: "example.Record", id: "rec_1", groups: ["example.files", "example.pages"] });
    expect(options).toEqual({ enabled: true, models: ["example.File", "example.Page"] });
  }
});

test("changing eligible groups preserves independent count-hook lifetimes", () => {
  const groups = {
    first: { content: { label: "First", edge: { model: "example.Edge" },
      usePaneCount: () => React.useMemo(() => 2, []), render: () => null } },
    second: { content: { label: "Second", edge: { model: "other.Edge" },
      usePaneCount: () => React.useState(3)[0], render: () => null } },
  };
  const containers = containersFromChildren(CHATTER_CONTAINERS, { "related#groups": groups });
  const onCount = vi.fn();
  const view = (edges: string[]) => <AppRuntimeProvider runtime={{ containers }}>
    <RelatedCountProbe id="related" context={{ ...context, route: { ...context.route!, recordEdges: edges } }}
      onCount={onCount} includePaneCounts />
  </AppRuntimeProvider>;
  const rendered = render(view(["example.Edge", "other.Edge"]));
  expect(onCount).toHaveBeenLastCalledWith("related", 5);
  rendered.rerender(view(["other.Edge"]));
  expect(onCount).toHaveBeenLastCalledWith("related", 3);
});

test("row-derived counts stay dormant until the Links pane is active", () => {
  const rows = vi.fn(() => 3);
  const groups = { lazy: { content: { label: "Lazy", usePaneCount: rows, render: () => null } } };
  const containers = containersFromChildren(CHATTER_CONTAINERS, { "related#groups": groups });
  const onCount = vi.fn();
  const probe = (active: boolean) => <AppRuntimeProvider runtime={{ containers }}>
    <RelatedCountProbe id="related" context={context} onCount={onCount} includePaneCounts={active} />
  </AppRuntimeProvider>;
  const view = render(probe(false));
  expect(rows).not.toHaveBeenCalled();
  expect(onCount).toHaveBeenLastCalledWith("related", undefined);
  view.rerender(probe(true));
  expect(rows).toHaveBeenCalled();
  expect(onCount).toHaveBeenLastCalledWith("related", 3);
});

test("an incomplete badge requests no related counts beside a lazy group", () => {
  const rows = vi.fn(() => 3);
  const containers = containersFromChildren(CHATTER_CONTAINERS, { "related#groups": {
    counted: { content: { label: "Counted", count: { id: "example.counted", models: [] }, render: () => null } },
    lazy: { content: { label: "Lazy", usePaneCount: rows, render: () => null } },
  } });
  const onCount = vi.fn();
  render(<AppRuntimeProvider runtime={{ containers }}>
    <RelatedCountProbe id="related" context={context} onCount={onCount} />
  </AppRuntimeProvider>);
  expect(query).not.toHaveBeenCalled();
  expect(rows).not.toHaveBeenCalled();
  expect(onCount).toHaveBeenLastCalledWith("related", undefined);
});

test("active Links reuses a partitioning group's row count instead of also requesting its related count", () => {
  const rows = vi.fn(() => 3);
  const containers = containersFromChildren(CHATTER_CONTAINERS, {
    "related#groups": { example: { content: { label: "Example", count: { id: "example.group", models: [] },
      usePaneCount: rows, render: () => null } } },
  });
  render(<AppRuntimeProvider runtime={{ containers }}>
    <RelatedCountProbe id="related" context={context} onCount={vi.fn()} includePaneCounts />
    <RelatedPane context={context} />
  </AppRuntimeProvider>);
  expect(rows).toHaveBeenCalled();
  expect(query).not.toHaveBeenCalled();
});

test("one group component owns the label popover and rows across popover lifetimes", async () => {
  const mounted = vi.fn();
  function Group({ label }: { label: RelatedGroupLabel }) {
    const [value, setValue] = React.useState(0);
    React.useEffect(() => { mounted(); }, []);
    return <>
      {label(<PaneLabelPopover icon="plus" label="Add">
        <button onClick={() => setValue((value) => value + 1)}>Confirm</button>
        <Popover.Close>Close</Popover.Close>
      </PaneLabelPopover>)}
      <li><PaneRows><li><PaneRow title={`Uploaded ${value}`} /></li></PaneRows></li>
    </>;
  }
  render(<Example content={{
    label: "Files", usePaneCount: () => 0, useVerb: () => true,
    render: (_, label) => <Group label={label} />,
  }} />);
  const trigger = screen.getByRole("button", { name: "Add" });
  expect(trigger.closest("li")?.textContent).toContain("Files");
  fireEvent.click(trigger);
  fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
  expect(screen.getByText("Uploaded 1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull());
  fireEvent.click(trigger);
  fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
  expect(screen.getByText("Uploaded 2")).toBeTruthy();
  expect(mounted).toHaveBeenCalledTimes(1);
});

test("useVerb controls the label action and empty-group presence", () => {
  const content: RelatedGroupContent = {
    label: "Files", usePaneCount: () => 1, useVerb: () => false,
    render: (_, label) => <>{label(<button>Add</button>)}<li>One file</li></>,
  };
  const view = render(<Example content={content} />);
  expect(screen.getByText("One file")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
  expect(screen.getByText("Files").closest("li")?.textContent).toBe("Files· 1");
  view.rerender(<Example content={{ ...content, usePaneCount: () => 0 }} />);
  expect(screen.queryByText("Files")).toBeNull();
});

test("count probes never mount the group's action or write state", () => {
  query.mockReturnValue(groupCounts({ "example.files": 3 }));
  const group = vi.fn(() => <li>Rows</li>);
  render(<Example probe content={{ label: "Files", count: { id: "example.files", models: [] }, render: group }} />);
  expect(screen.getByText("Related 3")).toBeTruthy();
  expect(group).not.toHaveBeenCalled();
});

test("edge eligibility precedes probes and custom narrowing", () => {
  const count = vi.fn(() => 2);
  const when = vi.fn(() => true);
  const group = vi.fn((_: ChatterViewContext, label: RelatedGroupLabel) => <>{label()}<li>Rows</li></>);
  const content = { label: "References", edge: { model: "unsupported.Edge" }, when, usePaneCount: count, render: group };
  const view = render(<Example content={content} />);
  expect(count).not.toHaveBeenCalled();
  expect(when).not.toHaveBeenCalled();
  expect(group).not.toHaveBeenCalled();
  view.rerender(<Example content={{ ...content, edge: { model: "example.Edge" } }} />);
  expect(screen.getByText("References").closest("li")?.textContent).toBe("References· 2");
  view.rerender(<Example content={{ ...content, edge: { model: "example.Edge" }, when: () => false }} />);
  expect(screen.queryByText("References")).toBeNull();
});
