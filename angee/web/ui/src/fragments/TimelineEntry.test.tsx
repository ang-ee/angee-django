// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { TimelineEntry } from "./TimelineEntry";

describe("TimelineEntry", () => {
  afterEach(() => {
    cleanup();
  });

  test("keeps 160-character excerpts intact and truncates longer text", () => {
    const exact = "a".repeat(160);
    const { container, rerender } = render(
      <TimelineEntry title="Updated" timestamp={null} body={exact} />,
    );
    expect(container.textContent).toContain(exact);

    const over = "b".repeat(161);
    rerender(<TimelineEntry title="Updated" timestamp={null} body={over} />);

    expect(container.textContent).toContain(`${"b".repeat(157)}...`);
    expect(container.textContent).not.toContain(over);
  });
});
