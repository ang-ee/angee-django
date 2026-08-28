// @vitest-environment happy-dom

import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { many2manyWidget } from "./many2many";

describe("many2manyWidget", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders nested relation records by their option labels", () => {
    const Read = many2manyWidget.read;

    render(
      <Read
        value={[{ id: "skill-1" }, { id: "skill-2" }]}
        field={{
          options: [
            { value: "skill-1", label: "Planning" },
            { value: "skill-2", label: "Review" },
          ],
        }}
      />,
    );

    expect(screen.getByText("Planning")).toBeTruthy();
    expect(screen.getByText("Review")).toBeTruthy();
  });
});
