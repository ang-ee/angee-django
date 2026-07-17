// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { ErrorBanner } from "./ErrorBanner";

describe("ErrorBanner", () => {
  test("announces an appearing error summary", () => {
    render(<ErrorBanner description="Resolution failed" />);

    expect(screen.getByRole("alert").textContent).toContain(
      "Resolution failed",
    );
  });
});
