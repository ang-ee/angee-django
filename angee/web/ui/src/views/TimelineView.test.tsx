// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { TimelineView } from "./TimelineView";

afterEach(cleanup);

interface Event extends Record<string, unknown> {
  id: string;
  who: string;
  body: string;
  at: string;
}

const ROWS: Event[] = [
  { id: "1", who: "Mira", body: "Tagged it", at: "2026-05-27T10:00:00Z" },
  { id: "2", who: "Sam", body: "Approved", at: "2026-05-26T09:00:00Z" },
  { id: "3", who: "Alexis", body: "Created", at: "2026-05-27T08:00:00Z" },
];

describe("TimelineView", () => {
  test("buckets rows by day and renders newest day first", () => {
    render(
      <TimelineView<Event>
        rows={ROWS}
        dateField="at"
        titleField="who"
        bodyField="body"
      />,
    );
    expect(screen.getByText("Mira")).toBeTruthy();
    expect(screen.getByText("Tagged it")).toBeTruthy();
    // Two day headers (27th and 26th).
    const days = screen.getAllByText(/2026/);
    expect(days.length).toBe(2);
    // Newest day header is first in the DOM.
    expect(days[0]?.textContent).toContain("May 27");
  });

  test("renderEntry overrides the default entry body", () => {
    render(
      <TimelineView<Event>
        rows={ROWS}
        dateField="at"
        renderEntry={(row) => <span>custom:{row.who}</span>}
      />,
    );
    expect(screen.getByText("custom:Mira")).toBeTruthy();
  });
});
