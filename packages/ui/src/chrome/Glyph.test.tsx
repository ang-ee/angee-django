// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { Glyph } from "./Glyph";

describe("Glyph", () => {
  test("adapts the logo owner's SVG through the base registry", () => {
    const { container } = render(<Glyph name="angee" size={20} />);

    const logo = container.querySelector("svg");
    expect(logo).not.toBeNull();
    expect(logo?.getAttribute("width")).toBe("20");
    expect(container.querySelector(".angee-scene")).toBeNull();
  });

  test("resolves the generic refresh verb glyph from the base registry", () => {
    const { container } = render(<Glyph name="refresh" />);

    expect(container.querySelector("svg")).not.toBeNull();
  });
});
