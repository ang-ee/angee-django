// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { defaultWidgets } from "./index";

describe("booleanBadge widget", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders true and false labels as badges", () => {
    const Badge = defaultWidgets.booleanBadge.read;

    render(
      <>
        <Badge value />
        <Badge
          value={false}
          field={{
            options: [
              { value: "true", label: "Staff" },
              { value: "false", label: "Member" },
            ],
          }}
        />
      </>,
    );

    expect(screen.getByText("True").className).toContain("bg-success-soft");
    expect(screen.getByText("Member").className).toContain("bg-inset");
  });
});
