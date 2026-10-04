// @vitest-environment happy-dom

import { testDataResource } from "@angee/metadata/testing";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider, createRouteHref } from "../runtime";
import { createUiTestProviders } from "../testing";
import { RecordIssues } from "./RecordIssues";

const { Provider, clearClients } = createUiTestProviders({
  resources: [testDataResource("notes.Note")],
});
afterEach(() => { cleanup(); clearClients(); });

describe("RecordIssues", () => {
  test.each([
    { tone: "info", role: "status", fill: "bg-info-soft" },
    { tone: "success", role: "status", fill: "bg-success-soft" },
    { tone: "warning", role: "status", fill: "bg-warning-soft" },
    { tone: "danger", role: "alert", fill: "bg-danger-soft" },
  ] as const)("renders and announces the $tone severity", ({ tone, role, fill }) => {
    render(<RecordIssues items={[{ id: "title", tone, message: "Review the title" }]} />);

    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    const issue = screen.getByRole(role);
    expect(issue.textContent).toBe("Review the title");
    expect(issue.classList.contains(fill)).toBe(true);
    expect(issue.querySelector(".glyph")).toBeTruthy();
  });

  test.each([undefined, "details"])("focuses a field with tab %s", (recordTabId) => {
    const focus = vi.fn((field: string) => document.getElementById(field)?.focus());
    render(<>
      <input id="title" aria-label="Title" />
      <RecordIssues
        items={[{ id: "title-issue", tone: "warning", message: "Enter a title", field: "title", recordTabId }]}
        onFocusField={focus}
      />
    </>);

    const action = screen.getByRole("button", { name: "Enter a title" });
    expect(action.getAttribute("type")).toBe("button");
    fireEvent.click(action);
    expect(focus).toHaveBeenCalledWith("title", recordTabId ? { recordTabId } : undefined);
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Title" }));
  });

  test("links a record through its registered route and the shared navigator", () => {
    const navigate = vi.fn();
    render(<Provider navigate={navigate}><AppRuntimeProvider runtime={{
      routesByResource: { "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } } },
      routeHref: createRouteHref([{ name: "notes.record", path: "/notes/$id" }]),
    }}>
      <RecordIssues items={[{
        id: "related", tone: "info", message: "Review the related note", record: { resource: "notes.Note", id: "note 1" },
      }]} />
    </AppRuntimeProvider></Provider>);

    const link = screen.getByRole("link", { name: "Review the related note" });
    expect(link.getAttribute("href")).toBe("/notes/note%201");
    fireEvent.click(link);
    expect(navigate).toHaveBeenCalledWith("/notes/note%201");
  });

  test("keeps messages readable when their targets are unavailable", () => {
    render(<Provider><AppRuntimeProvider runtime={{}}>
      <RecordIssues items={[
        { id: "field", tone: "warning", message: "Enter a title", field: "title" },
        { id: "record", tone: "info", message: "Review the related note", record: { resource: "notes.Note", id: "note-1" } },
      ]} />
    </AppRuntimeProvider></Provider>);

    expect(screen.getByText("Enter a title")).toBeTruthy();
    expect(screen.getByText("Review the related note")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  test("renders nothing when the issue list becomes empty", () => {
    const { container, rerender } = render(<RecordIssues items={[{ id: "title", tone: "danger", message: "Title missing" }]} />);
    expect(screen.getByRole("alert")).toBeTruthy();

    rerender(<RecordIssues items={[]} />);

    expect(container.childElementCount).toBe(0);
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
