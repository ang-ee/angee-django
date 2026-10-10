// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { MetaGrid, MetaSection } from "./MetaGrid";

afterEach(cleanup);

test("metadata rows share the collection label column and preserve pair semantics", () => {
  const { container } = render(
    <MetaGrid
      rows={[
        ["Owner", "Sofia Marin"],
        {
          action: <button type="button">Copy</button>,
          id: "status",
          label: "Status",
          value: "Ready",
        },
      ]}
    />,
  );
  const grid = container.querySelector("dl");

  expect(grid?.className).toContain("grid-cols-[max-content_minmax(0,1fr)]");
  expect(Array.from(grid?.children ?? [])).toHaveLength(2);
  expect(
    Array.from(grid?.children ?? []).every((row) =>
      row.className.includes("contents"),
    ),
  ).toBe(true);
  expect(grid?.querySelectorAll("dt")).toHaveLength(2);
  expect(grid?.querySelectorAll("dd")).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
});

test("metadata sections preserve the default heading and allow semantic nesting", () => {
  render(<>
    <MetaSection title="Existing section">Existing content</MetaSection>
    <MetaSection title="Page section" headingLevel={2}>
      <MetaSection title="Nested detail" headingLevel={4}>Retained evidence</MetaSection>
    </MetaSection>
  </>);
  expect(screen.getByRole("heading", { level: 3, name: "Existing section" })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 2, name: "Page section" })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 4, name: "Nested detail" })).toBeTruthy();
  expect(screen.getByText("Retained evidence")).toBeTruthy();
});
