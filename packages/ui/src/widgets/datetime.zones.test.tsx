// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { datetimeWidget } from "./datetime";

const Edit = datetimeWidget.edit;

afterEach(cleanup);

// A zone suite, run in the runner's zone and under each zone the shared Vitest defaults pin: the
// widget edits in the viewer's zone and stores the instant meant, which a server in any zone reads
// back as that instant. A naive wall time would be read in the server's zone instead.
describe("datetime widget", () => {
  test("a time picked in the viewer's zone is stored as that instant, with the viewer's offset", async () => {
    const onChange = vi.fn();
    const stored = "2026-11-20T06:00:00+00:00";
    render(<Edit value={stored} onChange={onChange} field={{ label: "Start" }} />);

    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    fireEvent.change(await screen.findByLabelText("Time"), { target: { value: "10:30" } });

    // The same day the viewer sees, at 10:30 on the viewer's own clock.
    const meant = new Date(stored);
    meant.setHours(10, 30, 0, 0);
    const written = onChange.mock.lastCall?.[0] as string;
    expect(written).toMatch(/^\d{4}-\d{2}-\d{2}T10:30:00[+-]\d{2}:\d{2}$/);
    expect(Date.parse(written)).toBe(meant.getTime());
  });
});
