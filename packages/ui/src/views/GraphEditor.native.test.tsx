// @vitest-environment happy-dom
import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../testing";
import { Input } from "../ui/input";
import { GraphEditor, type GraphEditorLayout, type GraphEditorSelection } from "./GraphEditor";

const { Provider } = createUiTestProviders();
beforeAll(() => {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});
afterEach(cleanup);

test("controlled selection returns to a canvas node after an external selection", async () => {
  const changed = vi.fn();
  function Example() {
    const [selected, setSelected] = React.useState<GraphEditorSelection>({ nodes: [], link: null });
    return <>
      <button type="button" onClick={() => setSelected({ nodes: ["b"], link: null })}>Select Beta externally</button>
      <GraphEditor
        nodes={[{ id: "a", kind: "node", title: "Alpha", ports: [] }, { id: "b", kind: "node", title: "Beta", ports: [] }]}
        links={[]} layout={{}} selected={selected}
        onSelectionChange={(next) => { changed(next); setSelected(next); }}
        canLink={() => true} onLink={vi.fn()} onUnlink={vi.fn()} onDelete={vi.fn()}
        onLayoutChange={vi.fn()} onInsertOnLink={vi.fn()} onAddFromPort={vi.fn()}
      />
    </>;
  }
  render(<Example />, { wrapper: Provider });
  const alpha = await screen.findByTestId("rf__node-a");
  fireEvent.click(alpha);
  await waitFor(() => expect(alpha.className).toContain("selected"));
  fireEvent.click(screen.getByRole("button", { name: "Select Beta externally" }));
  await waitFor(() => expect(alpha.className).not.toContain("selected"));
  fireEvent.click(alpha);
  await waitFor(() => expect(changed).toHaveBeenLastCalledWith({ nodes: ["a"], link: null }));
  expect(alpha.className).toContain("selected");
});

test("native multi-selection moves both nodes in one layout update and deletes only by intent", async () => {
  const moved = vi.fn();
  const deleted = vi.fn();
  function Example() {
    const [layout, setLayout] = React.useState<GraphEditorLayout>({ a: { x: 20, y: 30 }, b: { x: 200, y: 30 } });
    return <GraphEditor className="h-[600px] w-[800px]"
      nodes={[
        { id: "a", kind: "node", title: "Alpha", ports: [{ id: "next" }], detail: <Input aria-label="Node annotation" className="nodrag" defaultValue="Note" /> },
        { id: "b", kind: "node", title: "Beta", ports: [] },
      ]}
      links={[]} layout={layout} canLink={() => true}
      onLink={vi.fn()} onUnlink={vi.fn()} onDelete={deleted}
      onLayoutChange={(next) => { moved(next); setLayout(next); }}
      onInsertOnLink={vi.fn()} onAddFromPort={vi.fn()} />;
  }
  render(<Example />, { wrapper: Provider });
  const first = await screen.findByTestId("rf__node-a");
  const second = screen.getByTestId("rf__node-b");
  fireEvent.keyDown(first, { key: "Enter" });
  fireEvent.keyUp(first, { key: "Enter" });
  await waitFor(() => expect(first.className).toContain("selected"));
  const modifier = navigator.userAgent.includes("Mac") ? "Meta" : "Control";
  fireEvent.keyDown(window, { key: modifier });
  fireEvent.keyDown(second, { key: "Enter", ctrlKey: modifier === "Control", metaKey: modifier === "Meta" });
  fireEvent.keyUp(second, { key: "Enter", ctrlKey: modifier === "Control", metaKey: modifier === "Meta" });
  await waitFor(() => expect(second.className).toContain("selected"));
  fireEvent.keyUp(window, { key: modifier });
  expect(first.className).toContain("selected");

  fireEvent.keyDown(first, { key: "ArrowRight" });
  await waitFor(() => expect(moved).toHaveBeenCalledExactlyOnceWith({ a: { x: 25, y: 30 }, b: { x: 205, y: 30 } }));
  // The observer stub leaves native nodes unmeasured; labels still identify their controls.
  fireEvent.keyDown(screen.getByLabelText("Node annotation"), { key: "Backspace" });
  expect(deleted).not.toHaveBeenCalled();
  fireEvent.keyDown(first, { key: "Delete" });
  expect(deleted).toHaveBeenCalledExactlyOnceWith(["a", "b"]);
  expect(screen.getByTestId("rf__node-a")).toBeTruthy();
  expect(screen.getByTestId("rf__node-b")).toBeTruthy();
});
