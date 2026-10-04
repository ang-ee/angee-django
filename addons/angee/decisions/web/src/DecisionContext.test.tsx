// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ShellPageTestProviders } from "@angee/app/testing";

import { DecisionContext } from "./DecisionContext";

const mocks = vi.hoisted(() => ({ openRecord: vi.fn() }));
vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, { useRecordPeek: () => mocks.openRecord });
});
afterEach(() => { cleanup(); mocks.openRecord.mockReset(); });

describe("decision context", () => {
  test("shows attributed facts and evidence and passes complete locators to the native record peek", async () => {
    const evidence = { model: "notes.Note", id: "nte_evidence", label: "Evidence note", tab: "source", page: 2, search: { query: "passage", stale: null } };
    render(<ShellPageTestProviders><DecisionContext context={{
      facts: [{ pointer: "/count", label: "Count", value: 7, authority: "source", evidence: [evidence] }],
      references: [{ model: "notes.Note", id: "nte_related", label: "Related note" }],
    }} /></ShellPageTestProviders>);
    expect(await screen.findByText("7")).toBeTruthy();
    expect(screen.getByText("Count")).toBeTruthy();
    expect(screen.getByText("Source")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Facts", level: 2 })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Evidence", level: 3 })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "References", level: 2 })).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "References" })).getByRole("button", { name: "Related note" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Evidence note" }));
    expect(mocks.openRecord).toHaveBeenCalledWith(evidence);

  });

  test("renders nothing for empty context", () => {
    const { container } = render(<ShellPageTestProviders><DecisionContext context={{}} /></ShellPageTestProviders>);
    expect(container.textContent).toBe("");
  });

  test("shows structured facts as readable fields without a JSON code block", () => {
    const { container } = render(<ShellPageTestProviders><DecisionContext context={{ facts: [{
      pointer: "/review", label: "Review", authority: "source", value: {
        source_name: "Draft", reviewed: true, lines: [{ line_number: 1, text: "Ready" }],
      },
    }] }} /></ShellPageTestProviders>);
    expect(screen.getByText("Source Name")).toBeTruthy();
    expect(screen.getByText("Draft")).toBeTruthy();
    expect(screen.getByText("Line Number")).toBeTruthy();
    expect(screen.getByText("Ready")).toBeTruthy();
    expect(container.querySelector("pre, code")).toBeNull();
  });

  test.each([
    { facts: [{ label: "Malformed", authority: "source", value: 2 }] },
    { references: [{ model: "notes.Note", id: "nte_bad", page: "two" }] },
    { facts: [], references: [], evidence: [] },
  ])("shows a readable error for unsupported retained context", (context) => {
    render(<ShellPageTestProviders><DecisionContext context={context} /></ShellPageTestProviders>);
    expect(screen.getByRole("alert").textContent).toContain("Context is unavailable.");
  });
});
