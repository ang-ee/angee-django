// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"navigation":{"disableMainFrameNavigation":true,"disableChildPageNavigation":true}}}
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { InAppLinkProvider } from "../lib/in-app-link";
import { PaneRow } from "./pane-list";

afterEach(cleanup);

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
