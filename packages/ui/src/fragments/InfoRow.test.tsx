// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { InfoRow } from "./InfoRow";

afterEach(cleanup);

test("preserves its root overrides and dt/dd adapter contract", () => {
  const { container } = render(
    <InfoRow
      action={<button type="button">Copy</button>}
      className="px-0 py-0 text-2xs"
      label="Model"
      value="gpt"
    />,
  );
  const root = container.firstElementChild;

  expect(root?.tagName).toBe("DIV");
  expect(root?.className).toContain("px-0");
  expect(root?.className).toContain("py-0");
  expect(root?.className).toContain("text-2xs");
  expect(root?.querySelector("dt")?.textContent).toBe("Model");
  expect(root?.querySelector("dd")?.textContent).toContain("gpt");
  expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
});
