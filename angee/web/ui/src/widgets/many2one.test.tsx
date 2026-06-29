// @vitest-environment happy-dom

import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { many2oneWidget } from "./many2one";

describe("many2oneWidget", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders a nested relation record by its option label", () => {
    const Read = many2oneWidget.read;

    render(
      <Read
        value={{ id: "model-1" }}
        field={{
          options: [{ value: "model-1", label: "Claude Opus" }],
        }}
      />,
    );

    expect(screen.getByText("Claude Opus")).toBeTruthy();
  });

  test("falls back to the nested relation id without rendering an object", () => {
    const Read = many2oneWidget.read;

    render(<Read value={{ id: "model-1" }} />);

    expect(screen.getByText("model-1")).toBeTruthy();
  });
});
