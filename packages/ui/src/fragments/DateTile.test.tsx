// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { DateTile } from "./DateTile";

describe("DateTile", () => {
  test("reads the month over the day; the full date is the face's tooltip and is spoken once", () => {
    const { container } = render(<DateTile value="2026-09-28T22:30:00Z" timeZone="UTC" />);
    const time = container.querySelector("time")!;
    expect(time.getAttribute("datetime")).toBe("2026-09-28");
    expect(time.hasAttribute("title")).toBe(false);
    const face = screen.getByText("Sep").parentElement!;
    expect(face.getAttribute("aria-hidden")).toBe("true");
    expect(face.getAttribute("title")).toBe("Sep 28, 2026");
    expect(screen.getByText("28").className).toContain("font-display");
    expect(screen.getAllByText("Sep 28, 2026")).toHaveLength(1);
    expect(screen.getByText("Sep 28, 2026").className).toContain("sr-only");
  });

  test("takes the calendar day in the given time zone, for the face and datetime", () => {
    const { container } = render(<DateTile value="2026-09-28T22:30:00Z" timeZone="Pacific/Kiritimati" />);
    expect(screen.getByText("Sep")).toBeTruthy();
    expect(screen.getByText("29")).toBeTruthy();
    expect(container.querySelector("time")!.getAttribute("datetime")).toBe("2026-09-29");
  });

  test("shows the pending state for a missing or unreadable date", () => {
    const { container, rerender } = render(<DateTile value={null} />);
    expect(screen.getByText("Date pending")).toBeTruthy();
    expect(container.querySelector("time")).toBeNull();
    rerender(<DateTile value="not a date" pendingLabel="To be scheduled" />);
    expect(screen.getByText("To be scheduled")).toBeTruthy();
  });

  test("sizes the day through the recipe", () => {
    render(<DateTile value="2026-09-28T12:00:00Z" timeZone="UTC" size="lg" />);
    expect(screen.getByText("28").className).toContain("text-28");
  });
});
