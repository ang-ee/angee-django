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
});
