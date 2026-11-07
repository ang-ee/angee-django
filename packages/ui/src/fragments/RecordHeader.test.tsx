// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { RecordHeader } from "./RecordHeader";

afterEach(() => cleanup());

describe("RecordHeader", () => {
  test.each([
    ["compact", "h-5", "w-1/2"],
    ["comfortable", "h-7", "w-2/3"],
  ] as const)("a pending %s title is one skeleton line that announces its label", (density, height, width) => {
    render(
      <RecordHeader density={density} loading loadingMessage="Loading queue…" title="Engineering"
        crumbs="Board" description="Plan the work" />,
    );

    const heading = screen.getByRole("heading", { level: 1 });
    const status = screen.getByRole("status");
    expect(heading.contains(status)).toBe(true);
    expect(status.textContent).toBe("Loading queue…");
    const line = status.querySelector("[aria-hidden='true']");
    expect(line?.className).toContain(height);
    expect(line?.className).toContain(width);
    // The title waits; the header's other slots render as given.
    expect(screen.queryByText("Engineering")).toBeNull();
    expect(screen.getByText("Board")).toBeTruthy();
    expect(screen.getByText("Plan the work")).toBeTruthy();
  });

  test("a pending title falls back to the shared loading label", () => {
    render(<RecordHeader loading title={null} />);

    expect(screen.getByRole("status").textContent).toBe("Loading…");
  });

  test("an unavailable record reads the owner's title, and a pending read still wins", () => {
    const { rerender } = render(<RecordHeader unavailable title={undefined} />);
    expect(screen.getByRole("heading", { level: 1, name: "Record unavailable" })).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();

    rerender(<RecordHeader loading unavailable loadingMessage="Loading queue…" title={undefined} />);
    expect(screen.getByRole("status").textContent).toBe("Loading queue…");
    expect(screen.queryByText("Record unavailable")).toBeNull();
  });

  test("a settled title renders without a status", () => {
    render(<RecordHeader title="Engineering" />);

    expect(screen.getByRole("heading", { level: 1, name: "Engineering" })).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
