// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, test } from "vitest";

import { contrastWarningsWidget } from "./ContrastWarnings";

afterEach(cleanup);

describe("contrastWarningsWidget.read", () => {
  test("shows one warning per moved role, naming its seed by the theme's label", () => {
    const { container } = render(createElement(contrastWarningsWidget.read, {
      value: [{ seed: "BRAND", scheme: "LIGHT", role: "LINK" }],
    }));
    expect(container.textContent).toContain(
      "Brand color: a link in light mail moves far from this colour to stay readable.",
    );
    expect(container.querySelectorAll("[role=status]")).toHaveLength(1);
  });

  test("drops a seed, scheme or role the server does not send", () => {
    const { container } = render(createElement(contrastWarningsWidget.read, {
      value: [
        { seed: "ACCENT", scheme: "LIGHT", role: "LINK" },
        { seed: "BRAND", scheme: "SEPIA", role: "LINK" },
        { seed: "BRAND", scheme: "LIGHT", role: "BORDER" },
        { seed: "toString", scheme: "LIGHT", role: "LINK" },
      ],
    }));
    expect(container.querySelector("[role=status]")).toBeNull();
    expect(container.textContent).toContain("Light and dark mail show these colours as stated.");
  });

  test("says mail wears the colours as stated when the kit reports nothing", () => {
    const { container } = render(createElement(contrastWarningsWidget.read, { value: [] }));
    expect(container.querySelector("[role=status]")).toBeNull();
    expect(container.textContent).toContain("Light and dark mail show these colours as stated.");
  });
});
