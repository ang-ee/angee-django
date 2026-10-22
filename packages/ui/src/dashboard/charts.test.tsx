// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DashboardBars, DashboardDonut } from "./charts";

afterEach(cleanup);

test("stacked bars retain declared order, signed values and exact selectable series", () => {
  const select = vi.fn();
  render(<DashboardBars title="Received bills" orientation="vertical" onSelect={select}
    series={[{ key: "accepted", label: "Accepted", tone: "success" }, { key: "returned", label: "Returned", tone: "danger" }]}
    points={[{ key: "feb", label: "February", values: { accepted: 2, returned: -1 } },
      { key: "jan", label: "January", values: { accepted: 3 } }]} />);
  const table = screen.getByRole("table");
  expect(within(table).getAllByRole("rowheader").map((row) => row.textContent)).toEqual(["February", "January"]);
  expect(within(table).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["2", "-1", "3", "0"]);
  const returned = screen.getByRole("button", { name: "February · Returned: -1" });
  expect(returned.style.left).toBe("0px");
  fireEvent.click(returned);
  expect(select).toHaveBeenCalledExactlyOnceWith({ key: "feb", seriesKey: "returned" });
});

test("a donut provides named native actions alongside the complete data table", () => {
  const select = vi.fn();
  render(<DashboardDonut title="Check results" onSelect={select} points={[
    { key: "risk", label: "Risk", value: 2, tone: "warning" },
    { key: "unresolved", label: "Unresolved", value: 1, tone: "muted" },
  ]} />);
  const risk = screen.getByRole("button", { name: /Risk.*2.*67%/ });
  expect(risk.tagName).toBe("BUTTON");
  fireEvent.click(risk);
  expect(select).toHaveBeenCalledExactlyOnceWith("risk");
  expect(within(screen.getByRole("table")).getAllByRole("rowheader").map((row) => row.textContent))
    .toEqual(["Risk", "Unresolved"]);
});

test("nonfinite values are omitted and an empty donut never invents a result", () => {
  render(<DashboardDonut title="Check results" emptyLabel="No results" points={[
    { key: "passed", label: "Passed", value: Number.NaN },
    { key: "risk", label: "Risk", value: 0 },
  ]} />);
  expect(screen.getByText("No results")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("table")).toBeNull();
});


test("horizontal noninteractive segments anchor to the top of their own track", () => {
  const { container } = render(<DashboardBars title="Attention" points={[{ key: "supplier", label: "Supplier", value: 2 }]} />);
  const segment = container.querySelector<HTMLElement>('[title="Supplier: 2"]')!;
  expect(segment.style.top).toBe("0px");
  expect(segment.style.left).toBe("0%");
});
