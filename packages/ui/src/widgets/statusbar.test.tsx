// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { StatusbarSkeleton, StatusbarSteps, statusbarWidget } from "./statusbar";

const steps = [
  { value: "draft", label: "Draft", onPath: true, selectable: false },
  { value: "review", label: "Review", onPath: true, selectable: true, startDate: "2026-10-24", endDate: "2026-11-18", note: "Gate" },
  { value: "active", label: "Active", onPath: true, selectable: true },
  { value: "removed", label: "Removed", onPath: false, date: "2026-09-24" },
];

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("StatusbarSteps", () => {
  test("renders only path steps, with a muted off-path state chip", () => {
    render(<StatusbarSteps steps={steps} value="REMOVED" />);
    expect(screen.queryByRole("button", { name: "Removed" })).toBeNull();
    expect(screen.getByText("Removed · Sep 24, 2026")).toBeTruthy();
    expect(screen.getByRole("list").textContent).toContain("Draft");
    expect(screen.getByRole("list").className).toContain("isolate");
    const first = screen.getByText("Draft").closest("[role='listitem']");
    expect(first?.className).toContain("bg-border-strong");
    expect(first?.firstElementChild?.className).toContain("clip-path");
  });

  test("uses only server selectable steps and shows completed, current and upcoming", () => {
    const onChange = vi.fn();
    render(<StatusbarSteps steps={steps} value="review" onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Draft" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Review").closest("[role='listitem']")?.getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("Draft").closest("[role='listitem']")?.querySelector("svg")).toBeTruthy();
    expect(screen.getByText(/Gate/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Active" }));
    expect(onChange).toHaveBeenCalledWith("active");
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  test("collapses below measured step width", async () => {
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(500);
    render(<StatusbarSteps steps={steps} value="review" containerWidth={180} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Review.*2 of 3/ })).toBeTruthy());
  });

  test("re-expands after its parent grows", async () => {
    let width = 180;
    let resize: (() => void) | undefined;
    const container = document.createElement("div");
    document.body.append(container);
    Object.defineProperty(container, "clientWidth", { get: () => width });
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(500);
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: () => void) { resize = callback; }
      observe() { /* Observations are delivered below. */ }
      disconnect() { /* No pending observations. */ }
    });
    render(<StatusbarSteps steps={steps} value="review" />, { container });
    await waitFor(() => expect(screen.getByRole("button", { name: /Review.*2 of 3/ })).toBeTruthy());
    act(() => { width = 700; resize?.(); });
    await waitFor(() => expect(screen.queryByRole("button", { name: /Review.*2 of 3/ })).toBeNull());
    expect(screen.getByRole("list").getAttribute("aria-hidden")).toBeNull();
  });

  test("resolves enum member casing and changes a plain form option", () => {
    const onChange = vi.fn();
    const Statusbar = statusbarWidget.edit;
    render(<Statusbar value="REVIEW" field={{ options: [{ value: "draft", label: "Draft" }, { value: "review", label: "In review" }] }} onChange={onChange} />);
    expect(screen.getByText("In review").closest("[role='listitem']")?.getAttribute("aria-current")).toBe("step");
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    expect(onChange).toHaveBeenCalledWith("draft");
  });

  test("shows the shaped loading bar at the one-line and two-line heights", () => {
    const { rerender } = render(<StatusbarSkeleton count={3} fill />);
    expect(screen.getByRole("status").children).toHaveLength(4);
    expect(screen.getByRole("status").className).toContain("h-8");
    expect(screen.getByRole("status").textContent).toContain("Loading status");
    rerender(<StatusbarSkeleton count={3} fill twoLine />);
    expect(screen.getByRole("status").className).toContain("h-12");
  });
});
