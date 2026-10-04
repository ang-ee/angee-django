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
