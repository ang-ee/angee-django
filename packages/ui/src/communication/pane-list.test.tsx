// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"navigation":{"disableMainFrameNavigation":true,"disableChildPageNavigation":true}}}
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { InAppLinkProvider } from "../lib/in-app-link";
import { PaneRow, PaneRowsSkeleton } from "./pane-list";

test.each([false, true])("href rows compose native navigation and preload, with a leading control: %s", (leading) => {
  vi.useFakeTimers();
  try {
    const navigate = vi.fn();
    const preload = vi.fn();
    render(<InAppLinkProvider navigate={navigate} preload={{ preload, delay: 50 }}>
      <PaneRow title="Record" href="/records/7" leading={leading ? <input type="checkbox" aria-label="Select" /> : undefined} />
    </InAppLinkProvider>);
    const link = screen.getByRole("link", { name: "Record" });
    for (const modifier of [{ metaKey: true }, { ctrlKey: true }, { button: 1 }]) {
      expect(fireEvent.click(link, modifier)).toBe(true);
    }
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.mouseEnter(link);
    vi.advanceTimersByTime(50);
    expect(preload).toHaveBeenCalledExactlyOnceWith("/records/7");
    expect(fireEvent.click(link)).toBe(false);
    expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7");
  } finally { vi.useRealTimers(); }
});

test("rows without a destination or action remain readable and non-interactive", () => {
  const open = vi.fn();
  render(<><PaneRow title="Unavailable route" /><PaneRow title="Inspect" onOpen={open} /></>);
  expect(screen.getByText("Unavailable route").closest("button, a")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
  expect(open).toHaveBeenCalledOnce();
});

test("an empty reason is no reason: the row stays available", () => {
  const open = vi.fn();
  render(<PaneRow title="Ada" onOpen={open} disabledReason="" />);
  const row = screen.getByRole("button", { name: "Ada" });
  expect(row.hasAttribute("aria-disabled")).toBe(false);
  fireEvent.click(row);
  expect(open).toHaveBeenCalledOnce();
});

test("an unavailable row is listed with its whole reason, reachable by keyboard, and does nothing", () => {
  const open = vi.fn(), navigate = vi.fn();
  const reason = "Already connected as the calendar source of another workspace";
  render(<InAppLinkProvider navigate={navigate}>
    <PaneRow title="Ada" verb="Use" onOpen={open} disabledReason={reason} />
    <PaneRow title="Record" href="/records/7" disabled />
  </InAppLinkProvider>);

  // The reason describes the control rather than joining its name, and wraps instead of truncating.
  const refused = screen.getByRole("button", { name: "Ada", description: reason });
  expect(refused.getAttribute("aria-disabled")).toBe("true");
  expect(refused.hasAttribute("disabled")).toBe(false);
  expect(screen.getByText(reason).className).not.toContain("truncate");
  refused.focus();
  expect(document.activeElement).toBe(refused);
  fireEvent.click(refused);
  expect(open).not.toHaveBeenCalled();
  // No verb tooltip and no hover fill on a row nothing happens on.
  expect(refused.getAttribute("title")).toBeNull();
  expect(refused.parentElement?.className).not.toContain("hover:");

  // A disabled destination is not a link.
  expect(screen.queryByRole("link", { name: "Record" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Record" }));
  expect(navigate).not.toHaveBeenCalled();
});

test("primary hints do not displace row text and secondary controls stay independent", () => {
  const open = vi.fn(), related = vi.fn();
  render(<><PaneRow title="Sender" detail={0} verb="Results" onOpen={open} />
    <PaneRow title="File" onOpen={open} verb={<button onClick={related}>3 uses</button>} /></>);
  expect(screen.queryByText("Results")).toBeNull();
  expect(screen.getByRole("button", { name: "Sender · 0" }).title).toBe("Results");
  fireEvent.click(screen.getByRole("button", { name: "3 uses" }));
  expect(related).toHaveBeenCalledOnce();
  expect(open).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "3 uses" }).parentElement?.className).not.toContain("opacity-0");
});

test("outbound rows preserve native new-tab navigation", () => {
  const navigate = vi.fn();
  render(<InAppLinkProvider navigate={navigate}>
    <PaneRow title="Provider" href="https://example.test/source" target="_blank" rel="noopener" />
  </InAppLinkProvider>);
  const link = screen.getByRole("link");
  expect(link.getAttribute("target")).toBe("_blank");
  expect(fireEvent.click(link)).toBe(true);
  expect(navigate).not.toHaveBeenCalled();
});

test("pending pane rows keep the row geometry under one status and render no visible copy", () => {
  render(<PaneRowsSkeleton loadingLabel="Loading results" rowCount={3} />);
  const status = screen.getByRole("status");
  expect(status.textContent).toBe("Loading results");
  const rows = [...status.children].filter((child) => child.getAttribute("aria-hidden") === "true");
  expect(rows).toHaveLength(3);
  // Each row keeps the pane row's box: a leading glyph, then the title and second lines.
  for (const row of rows) {
    expect(row.className).toContain("px-2 py-2");
    expect(row.querySelectorAll(".rounded-full")).toHaveLength(3);
  }
  expect(screen.queryAllByText(/\S/, { ignore: ".sr-only, script, style" })).toEqual([]);
});
