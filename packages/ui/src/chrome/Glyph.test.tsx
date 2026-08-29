// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { Glyph } from "./Glyph";

describe("Glyph", () => {
  afterEach(cleanup);

  test("adapts the logo owner's SVG through the base registry", () => {
    const { container } = render(<Glyph name="angee" size={20} />);

    const logo = container.querySelector("svg");
    expect(logo).not.toBeNull();
    expect(logo?.getAttribute("width")).toBe("20");
    expect(container.querySelector(".angee-scene")).toBeNull();
  });
});
