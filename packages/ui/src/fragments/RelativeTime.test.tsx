// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { RelativeTime } from "./RelativeTime";

describe("RelativeTime", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders the fallback for invalid dates", () => {
    const { container } = render(
      <RelativeTime value="not-a-date" fallback="Unknown" />,
    );

    expect(container.textContent).toBe("Unknown");
  });

  test.each([
    ["an ISO string", "2026-08-21T12:30:00.000Z"],
    ["a Date", new Date("2026-08-21T12:30:00.000Z")],
    ["a timestamp", Date.parse("2026-08-21T12:30:00.000Z")],
  ])("accepts %s", (_label, value) => {
    const { container } = render(<RelativeTime value={value} addSuffix={false} />);

    expect(container.querySelector("time")?.dateTime).toBe(
      "2026-08-21T12:30:00.000Z",
    );
  });

  test.each(["", "not-a-date", Number.NaN, new Date(Number.NaN)])(
    "renders the fallback for invalid input %p",
    (value) => {
      const { container } = render(
        <RelativeTime value={value} fallback="Unknown" />,
      );

      expect(container.textContent).toBe("Unknown");
      expect(container.querySelector("time")).toBeNull();
    },
  );
});
