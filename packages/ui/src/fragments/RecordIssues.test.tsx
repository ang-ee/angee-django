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

  test("titles an issue by its field's label, so the same words under two fields read apart", () => {
    const labels: Record<string, string> = { variables: "Variables", sample: "Sample values" };
    const focus = vi.fn();
    render(<RecordIssues
      items={[
        { id: "variables", tone: "danger", message: "Give an object.", field: "variables" },
        { id: "sample", tone: "danger", message: "Give an object.", field: "sample" },
        { id: "hidden", tone: "danger", message: "Give a key.", field: "key" },
        { id: "record-wide", tone: "danger", message: "Nothing reads support_url." },
      ]}
      onFocusField={focus}
      fieldLabel={(field) => labels[field]}
    />);

    const issues = screen.getAllByRole("alert");
    expect(issues.map((issue) => issue.textContent)).toEqual([
      "VariablesGive an object.", "Sample valuesGive an object.", "Give a key.", "Nothing reads support_url.",
    ]);
    // The label is the issue's title, not part of the control's name; the control is described by it, so
    // two controls with the same words tell a screen reader which field each one goes to.
    const controls = screen.getAllByRole("button", { name: "Give an object." });
    expect(controls).toHaveLength(2);
    expect(controls.map((control) => document.getElementById(control.getAttribute("aria-describedby") ?? "")?.textContent))
      .toEqual(["Variables", "Sample values"]);
    // A field the form gives no label for: the control has nothing to be described by.
    expect(screen.getByRole("button", { name: "Give a key." }).hasAttribute("aria-describedby")).toBe(false);
    fireEvent.click(controls[1]!);
    expect(focus).toHaveBeenCalledWith("sample", undefined);
  });

  test("polite issues arrive in one region that is in the page before them, and interrupt nothing", () => {
    const view = render(<RecordIssues polite items={[]} />);
    // The region exists with no issues, so a screen reader knows it before anything arrives in it.
    const region = view.container.querySelector("[aria-live]")!;
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(region.textContent).toBe("");

    view.rerender(<RecordIssues polite items={[
      { id: "refusal", tone: "danger", message: "Give an object.", field: "variables" },
      { id: "note", tone: "info", message: "Two variables are unused." },
    ]} />);
    // The same region, now holding the issues; none of them is an alert or a live region of its own.
    expect(view.container.querySelector("[aria-live]")).toBe(region);
    expect(region.textContent).toBe("Give an object.Two variables are unused.");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    expect(region.querySelectorAll("[aria-live], [role=alert], [role=status]")).toHaveLength(0);
    // The tone and its colour stay.
    expect(region.querySelector(".bg-danger-soft")?.textContent).toBe("Give an object.");
    // Each issue is read whole when it changes: a changed message comes with its field's label.
    expect([...region.querySelectorAll("li")].map((issue) => issue.getAttribute("aria-atomic"))).toEqual(["true", "true"]);
  });

  test("without `polite` the fragment renders nothing while there is no issue, and a danger issue is an alert", () => {
    const view = render(<RecordIssues items={[]} />);
    expect(view.container.childElementCount).toBe(0);
    view.rerender(<RecordIssues items={[{ id: "refusal", tone: "danger", message: "Give an object." }]} />);
    expect(screen.getByRole("alert").textContent).toBe("Give an object.");
    expect(view.container.querySelector("[aria-live]")).toBeNull();
    expect(view.container.querySelector("li")?.hasAttribute("aria-atomic")).toBe(false);
  });

  test("without a label source an issue that names a field shows its message alone", () => {
    render(<RecordIssues items={[{ id: "variables", tone: "danger", message: "Give an object.", field: "variables" }]} />);
    expect(screen.getByRole("alert").textContent).toBe("Give an object.");
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
