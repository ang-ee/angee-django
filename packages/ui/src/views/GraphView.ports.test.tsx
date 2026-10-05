// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, expect, test } from "vitest";
import { createUiTestProviders } from "../testing";
import { GraphView, graphNodeStyle } from "./GraphView";

const { Provider } = createUiTestProviders();
beforeAll(() => {
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: class { observe() {} unobserve() {} disconnect() {} } });
});
afterEach(cleanup);

test("GraphNodeLabel preserves box geometry and caption rows through highlight and detail changes", () => {
  const base = { id: "a", kind: "node", title: "Alpha", code: "alpha", selected: true };
  const styles = { node: graphNodeStyle("gray", "neutral", { highlightedBorderColor: "blue" }) };
  const graph = (highlighted: boolean, detail: string) => <GraphView
    nodes={[{ ...base, highlighted, detail }]} edges={[]} nodeStyles={styles} />;
  const view = render(graph(false, ""), { wrapper: Provider });
  const geometry = () => {
    const box = screen.getByTestId("rf__node-a");
    const label = screen.getByText("Alpha").parentElement!.parentElement!;
    return {
      width: box.style.width, minHeight: box.style.minHeight, padding: box.style.padding,
      borderWidth: box.style.borderWidth, borderStyle: box.style.borderStyle, borderRadius: box.style.borderRadius,
      labelClass: label.className,
      rows: Array.from(label.children).map((row) => [row.tagName, row.className]),
    };
  };
  const before = geometry();
  const ring = screen.getByTestId("rf__node-a").style.boxShadow;
  expect(ring).toContain("5px var(--brand)");
  expect(before.rows).toHaveLength(3);
  expect(before.rows[2]?.[1]).toContain("min-h-[1lh]");
  for (const [highlighted, detail] of [[true, ""], [true, "Page 4"], [false, "Page 4"], [false, ""]] as const) {
    view.rerender(graph(highlighted, detail));
    expect(geometry()).toEqual(before);
    const box = screen.getByTestId("rf__node-a");
    expect(box.style.borderColor).toBe(highlighted ? "blue" : "gray");
    expect(box.style.boxShadow).toBe(ring);
    expect(screen.getByText("Alpha").parentElement!.parentElement!.lastElementChild?.textContent).toBe(detail);
  }
});

test("node headers omit a repeated type label and constrain distinct labels", () => {
  const styles = { node: graphNodeStyle("gray", "neutral", { width: 100 }) };
  const view = render(<GraphView nodes={[{ id: "a", kind: "node", title: "Review source", kindLabel: "Review source" }]}
    edges={[]} nodeStyles={styles} />, { wrapper: Provider });
  expect(screen.getAllByText("Review source")).toHaveLength(1);
  view.rerender(<GraphView nodes={[{ id: "a", kind: "node", title: "Review source", kindLabel: "Review a much longer type" }]}
    edges={[]} nodeStyles={styles} />);
  expect(screen.getByText("Review source").className).toContain("min-w-0");
  expect(screen.getByText("Review a much longer type").className).toContain("truncate");
});

test("renders named output ports and status without changing a node's kind", () => {
  const base = { id: "a", kind: "node", title: "Alpha", ariaLabel: "Alpha" };
  const styles = { node: graphNodeStyle("var(--border-strong)", "neutral") };
  const view = render(<GraphView nodes={[{ ...base, ports: [{ id: "left", label: "Left" }, { id: "right", label: "Right" }] }]} edges={[]} nodeStyles={styles} status={{ a: { label: "Ready", tone: "success" } }} />, { wrapper: Provider });
  // Status lives on each node; there is no full-graph live region to re-announce.
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByTestId("rf__node-a").textContent).toContain("Ready");
  expect(screen.getByLabelText("Left").getAttribute("data-handleid")).toBe("left");
  expect(screen.getByLabelText("Right").getAttribute("data-handleid")).toBe("right");
  expect(screen.getByText("node")).toBeTruthy();
  view.rerender(<GraphView nodes={[{ ...base, ports: [{ id: "next", label: "Next" }] }]} edges={[]} nodeStyles={styles} status={{ a: { label: "Paused", tone: "warning" } }} />);
  expect(screen.queryByLabelText("Left")).toBeNull();
  expect(screen.getByLabelText("Next").getAttribute("data-handleid")).toBe("next");
  expect(screen.getByTestId("rf__node-a").textContent).toContain("Paused");
});

test("distinguishes omitted default ports from an explicitly terminal node", () => {
  const nodes = [{ id: "a", kind: "node", title: "Alpha" }, { id: "b", kind: "node", title: "Beta", ports: [] }];
  render(<GraphView nodes={nodes} edges={[]} nodeStyles={{ node: graphNodeStyle("var(--border-strong)", "neutral") }} />, { wrapper: Provider });
  expect(screen.getAllByLabelText("Output")).toHaveLength(1);
  expect(screen.getAllByLabelText("Input")).toHaveLength(2);
});

test("horizontal outcomes sit on the trailing edge and update on direction changes", () => {
  const props = { nodes: [{ id: "a", kind: "node", title: "Alpha", ports: [{ id: "done", label: "Done" }, { id: "error", label: "Error" }] }], edges: [], nodeStyles: { node: graphNodeStyle("gray", "neutral") } };
  const view = render(<GraphView {...props} layout={{ rankdir: "LR" }} />, { wrapper: Provider });
  const done = screen.getByLabelText("Done");
  const error = screen.getByLabelText("Error");
  expect(done.className).toContain("react-flow__handle-right");
  expect(screen.getByLabelText("Input").className).toContain("react-flow__handle-left");
  expect(done.parentElement).not.toBe(error.parentElement);
  expect(done.parentElement?.className).toContain("relative");
  expect(done.parentElement?.textContent).toBe("Done");
  expect(error.parentElement?.textContent).toBe("Error");
  view.rerender(<GraphView {...props} layout={{ rankdir: "TB" }} />);
  expect(screen.getByLabelText("Done").className).toContain("react-flow__handle-bottom");
  expect(screen.getByLabelText("Input").className).toContain("react-flow__handle-top");
});
