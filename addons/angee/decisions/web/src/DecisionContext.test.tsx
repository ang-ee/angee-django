// @vitest-environment happy-dom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ShellPageTestProviders } from "@angee/app/testing";
import { createRouteHref, defaultWidgets } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { testDataResource } from "@angee/metadata/testing";

import { DecisionContext, FactValue } from "./DecisionContext";

const { Provider, clearClients } = createUiTestProviders({
  resources: [testDataResource("notes.Note"), testDataResource("storage.File"), testDataResource("knowledge.Page")],
});
const runtime = {
  routeHref: createRouteHref([
    { name: "notes.record", path: "/notes/$id" },
    { name: "storage.file", path: "/storage/$id" },
    { name: "knowledge.page", path: "/knowledge/$id" },
  ]),
  routesByResource: {
    "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } },
    "storage.File": { collection: "storage.files", record: { name: "storage.file", param: "id" } },
    "knowledge.Page": { collection: "knowledge", record: { name: "knowledge.page", param: "id" } },
  },
};
afterEach(() => { cleanup(); clearClients(); });

describe("decision context", () => {
  test("a proposed relation retains its readable reference label", () => {
    render(<Provider><ShellPageTestProviders runtime={runtime}><FactValue value="nte_related" relationModel="notes.Note"
      label="Related note" tab="details" search={{ query: "passage" }} /></ShellPageTestProviders></Provider>);
    expect(screen.getByRole("link", { name: "Related note" }).getAttribute("href")).toBe("/notes/nte_related?recordTab=details&query=passage");
    expect(screen.queryByText("nte_related")).toBeNull();
  });

  test("shows attributed facts and links evidence to its record tab and preview page", async () => {
    const evidence = { model: "storage.File", id: "fil_source", pointer: "/count", label: "Evidence file", tab: "preview", search: { previewPage: "fil_source:2", stale: null } };
    render(<Provider><ShellPageTestProviders runtime={runtime}><DecisionContext context={{
      facts: [{ pointer: "/count", label: "Count", value: 7, authority: "source", evidence: [evidence] }],
      references: [{ model: "notes.Note", id: "nte_related", pointer: "", label: "Related note" }],
    }} /></ShellPageTestProviders></Provider>);
    expect(await screen.findByText("7")).toBeTruthy();
    expect(screen.getByText("Count")).toBeTruthy();
    expect(screen.getByTitle("Source")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Facts", level: 2 })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Evidence", level: 3 })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "References", level: 2 })).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "References" })).getByRole("link", { name: "Related note" }).getAttribute("href")).toBe("/notes/nte_related");
    expect(screen.getByRole("link", { name: "Evidence file" }).getAttribute("href")).toBe("/storage/fil_source?recordTab=preview&previewPage=fil_source%3A2");
    expect(screen.queryByRole("button", { name: "Evidence file" })).toBeNull();
  });

  test("renders a knowledge page title and first paragraph through its record link", async () => {
    const getOne = vi.fn(async () => ({ data: {
      id: "knp_policy", title: "Posting policy",
      markdown: { body: "# Internal heading\nBills require approval before posting.\n\nThis paragraph stays on the page." },
    } }));
    render(<Provider dataProvider={{ getOne }}><ShellPageTestProviders runtime={{ ...runtime, widgets: defaultWidgets }}>
      <DecisionContext context={{ references: [{
        model: "knowledge.Page", id: "knp_policy", pointer: "", label: "Retained page label",
      }] }} />
    </ShellPageTestProviders></Provider>);

    const link = await screen.findByRole("link", { name: "Posting policy" });
    expect(link.getAttribute("href")).toBe("/knowledge/knp_policy");
    expect(await screen.findByText("Bills require approval before posting.")).toBeTruthy();
    expect(screen.queryByText("Internal heading")).toBeNull();
    expect(screen.queryByText("This paragraph stays on the page.")).toBeNull();
    expect(getOne).toHaveBeenCalledWith(expect.objectContaining({
      resource: "pages", id: "knp_policy",
      meta: expect.objectContaining({ fields: ["id", "title", { markdown: ["body"] }] }),
    }));
  });

  test("renders nothing for empty context", () => {
    const { container } = render(<ShellPageTestProviders><DecisionContext context={{}} /></ShellPageTestProviders>);
    expect(container.textContent).toBe("");
  });

  test("omits empty address components and reads formatted facts through registered widgets", () => {
    render(<ShellPageTestProviders runtime={{ widgets: defaultWidgets }}><DecisionContext context={{ facts: [
      { pointer: "/address", label: "Address", authority: "source", value: { street: "Oak Street", po_box: null, city: "" } },
      { pointer: "/date", label: "Date", authority: "source", value: "2026-10-04", widget: "date" },
    ] }} /></ShellPageTestProviders>);
    expect(screen.getByText("Oak Street")).toBeTruthy();
    expect(screen.queryByText("Po Box")).toBeNull();
    expect(screen.queryByText("City")).toBeNull();
    expect(screen.queryByText("2026-10-04")).toBeNull();
  });

  test("a percentage fact uses the native display widget while retaining its exact value", () => {
    const fact = Object.freeze({ pointer: "/variance", label: "Amount variance percent", authority: "source",
      value: "65.68738656987295825771324864", widget: "percent" });
    render(<ShellPageTestProviders runtime={{ widgets: defaultWidgets }}><DecisionContext context={{ facts: [fact] }} /></ShellPageTestProviders>);
    expect(screen.getByText("65.69%")).toBeTruthy();
    expect(screen.queryByText(fact.value)).toBeNull();
    expect(fact.value).toBe("65.68738656987295825771324864");
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
