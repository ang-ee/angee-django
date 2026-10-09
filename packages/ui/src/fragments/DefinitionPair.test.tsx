// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { DefinitionPair } from "./DefinitionPair";

afterEach(cleanup);

test("renders inline and stacked definition pairs with their declared layouts", () => {
  render(
    <>
      <dl>
        <DefinitionPair
          data-testid="inline-pair"
          label="Owner"
          layout="contents"
          value="Sofia Marin"
        />
      </dl>
      <DefinitionPair
        data-testid="stacked-pair"
        label="Revenue"
        orientation="stacked"
        value="24"
      />
    </>,
  );

  expect(screen.getByTestId("inline-pair").className).toContain("contents");
  expect(screen.getByTestId("stacked-pair").className).toContain("gap-y-1");
  expect(document.querySelectorAll("dt")).toHaveLength(2);
  expect(document.querySelectorAll("dd")).toHaveLength(2);
});

test("associates div labels and values without emitting definition elements", () => {
  const { container } = render(
    <DefinitionPair as="div" label="Status" value="Ready" />,
  );
  const label = screen.getByText("Status");
  const value = screen.getByText("Ready").parentElement;

  expect(label.id).toBeTruthy();
  expect(value?.getAttribute("aria-labelledby")).toBe(label.id);
  expect(container.querySelector("dt, dd")).toBeNull();
});

test("renders the declared empty value when the value is absent", () => {
  render(<DefinitionPair emptyValue="Not provided" label="Archived" />);

  expect(screen.getByText("Not provided")).toBeTruthy();
});

test("renders muted caption detail after the value", () => {
  render(
    <DefinitionPair
      detail="Acme Corp"
      label="Amount"
      orientation="stacked"
      value="$1,234.00"
    />,
  );

  const value = screen.getByText("$1,234.00");
  const detail = screen.getByText("Acme Corp");
  expect(detail.className).toContain("text-2xs");
  expect(detail.className).toContain("text-fg-muted");
  expect(value.compareDocumentPosition(detail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
