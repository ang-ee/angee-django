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

test("renders named output ports and status without changing a node's kind", () => {
  const base = { id: "a", kind: "node", title: "Alpha", ariaLabel: "Alpha" };
  const styles = { node: graphNodeStyle("var(--border-strong)", "neutral") };
  const view = render(<GraphView nodes={[{ ...base, ports: [{ id: "left", label: "Left" }, { id: "right", label: "Right" }] }]} edges={[]} nodeStyles={styles} status={{ a: { label: "Ready", tone: "success" } }} />, { wrapper: Provider });
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.getByRole("status").textContent).toBe("Alpha: Ready. ");
  expect(screen.getByLabelText("Left").getAttribute("data-handleid")).toBe("left");
  expect(screen.getByLabelText("Right").getAttribute("data-handleid")).toBe("right");
  expect(screen.getByText("node")).toBeTruthy();
  view.rerender(<GraphView nodes={[{ ...base, ports: [{ id: "next", label: "Next" }] }]} edges={[]} nodeStyles={styles} status={{ a: { label: "Paused", tone: "warning" } }} />);
  expect(screen.queryByLabelText("Left")).toBeNull();
  expect(screen.getByLabelText("Next").getAttribute("data-handleid")).toBe("next");
  expect(screen.getByRole("status").textContent).toBe("Alpha: Paused. ");
});

test("distinguishes omitted default ports from an explicitly terminal node", () => {
  const nodes = [{ id: "a", kind: "node", title: "Alpha" }, { id: "b", kind: "node", title: "Beta", ports: [] }];
  render(<GraphView nodes={nodes} edges={[]} nodeStyles={{ node: graphNodeStyle("var(--border-strong)", "neutral") }} />, { wrapper: Provider });
  expect(screen.getAllByLabelText("Output")).toHaveLength(1);
  expect(screen.getAllByLabelText("Input")).toHaveLength(2);
});
