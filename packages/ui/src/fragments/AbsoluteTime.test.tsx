// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { AbsoluteTime } from "./AbsoluteTime";

describe("AbsoluteTime", () => {
  test("reads the clock time with the full stamp as the tooltip", () => {
    render(<AbsoluteTime value="2026-10-20T14:02:00Z" timeZone="UTC" />);
    const time = screen.getByText("2:02 PM");
    expect(time.getAttribute("datetime")).toBe("2026-10-20T14:02:00.000Z");
    expect(time.getAttribute("title")).toContain("2026");
  });

  test("reads the day or the full stamp on request", () => {
    const { rerender } = render(<AbsoluteTime value="2026-10-20T14:02:00Z" timeZone="UTC" display="date" />);
    expect(screen.getByText(/Oct 20/)).toBeTruthy();
    rerender(<AbsoluteTime value="2026-10-20T14:02:00Z" timeZone="UTC" display="datetime" />);
    expect(screen.getByText(/Oct 20, 2026/).getAttribute("title")).toBeNull();
  });

  test("reads a bare calendar day as that date in any zone, with the full date as its tooltip", () => {
    render(<AbsoluteTime value="2026-11-20" display="date" timeZone="Pacific/Kiritimati" />);
    const time = screen.getByText(/^Nov 20/);
    expect(time.getAttribute("datetime")).toBe("2026-11-20");
    // A day has no clock time: never the local midnight it parses to.
    expect(time.getAttribute("title")).toBe("Nov 20, 2026");
  });

  test("renders the fallback for an unreadable value", () => {
    render(<AbsoluteTime value={null} fallback="—" />);
    expect(screen.getByText("—")).toBeTruthy();
  });
});
