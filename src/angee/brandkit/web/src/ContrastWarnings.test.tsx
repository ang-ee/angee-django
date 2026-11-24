// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, test } from "vitest";

import { contrastWarningsWidget } from "./ContrastWarnings";

afterEach(cleanup);

describe("contrastWarningsWidget.read", () => {
  test("shows one warning per moved role, titled with its seed's theme label", () => {
    const { container } = render(createElement(contrastWarningsWidget.read, {
      value: [{ seed: "brand", scheme: "LIGHT", role: "LINK" }],
    }));
    const text = container.textContent ?? "";
    expect(text).toContain("Brand color");
    expect(text).toContain("A link in light mail moves far from this colour to stay readable.");
    expect(container.querySelectorAll("[role=status]")).toHaveLength(1);
  });

  test("says mail wears the colours as stated when the kit reports nothing", () => {
    const { container } = render(createElement(contrastWarningsWidget.read, { value: [] }));
    expect(container.querySelector("[role=status]")).toBeNull();
    expect(container.textContent).toContain("Light and dark mail show these colours as stated.");
  });
});
