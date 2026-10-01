// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { MetaSection } from "./MetaGrid";

afterEach(cleanup);

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
