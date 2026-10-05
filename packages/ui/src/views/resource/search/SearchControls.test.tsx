// @vitest-environment happy-dom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ResourceQuery, ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { AppRuntimeProvider, containersFromChildren, type ComposedContainers } from "../../../runtime";
import { ResourceToolbar } from "../../../toolbars/ResourceToolbar";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "../resource-view-context";
import { RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY } from "../resource-view-favorites";
import { RESOURCE_CONTAINERS } from "../resource-view-kinds";
import { RowsListView } from "../RowsListView";
import { useSearchCatalog } from "./catalog";
import { useResourceSearch } from "./use-resource-search";
import { pageSearchShortcuts, validateSearchShortcutCatalog, type ListSearchDeclaration } from "./shortcuts";
import type { SearchFacet } from "./types";
import { developmentMode } from "../../../lib/development-mode";
import { SearchControls } from "./SearchControls";
import { searchFixture } from "./search-fixture.test-support";

vi.mock("../../../lib/development-mode", () => ({ developmentMode: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => {
  vi.mocked(developmentMode).mockReturnValue(true);
  // A roomy toolbar whose box chips fit: the box measures its narrow inert chip copies.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return new DOMRect(0, 0, this.closest("[inert]") ? 60 : 1024, 80);
  });
});
const query = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, "requester.display_name": { scalar: "String" },
  submitted: { scalar: "DateTime" }, due: { scalar: "Date" }, duration: { scalar: "Float" },
  priority: { kind: "enum", values: [{ value: "high" }, { value: "low" }] },
  status: { kind: "enum", values: [{ value: "open" }, { value: "closed" }] },
  owner: { kind: "relation" },
} });
const columns = [
  { field: "title", header: "Title" }, { field: "requester.display_name", header: "Filed by" },
  { field: "submitted", header: "Submitted" }, { field: "due", header: "Need by" },
  { field: "duration", header: "Duration" }, { field: "priority", header: "Priority" }, { field: "status", header: "Status" },
];
const filters = [{ id: "mine", label: "My records", filter: { owner: { exact: "viewer" } } }];
const declaration: ListSearchDeclaration = { shortcuts: [
  { kind: "text", field: "title" }, { kind: "text", field: "requester.display_name" },
  { kind: "clause", field: "submitted" }, { kind: "clause", field: "due" }, { kind: "clause", field: "duration" },
  { kind: "facet", field: "priority" }, { kind: "facet", field: "status" }, { kind: "toggle", id: "mine" },
] };
const blankStatus: SearchFacet = { field: "status", label: "Status", source: "scalar", options: [
  { id: "open", label: "Open", value: "open", filter: { status: { exact: "open" } } },
  { id: "closed", label: "Closed", value: "closed", filter: { status: { exact: "closed" } } },
  { id: "blank", label: "Not given", filter: { status: { isNull: true } } },
] };
const metadata = schemaFieldMetadataFromDataResources([testDataResource("notes.Note", { query: query.contract, recordRepresentation: "title" })]);
const model = metadata.labels["notes.Note"]!;

function fixture(searchDeclaration: ListSearchDeclaration = declaration, options: {
  containers?: ComposedContainers; route?: string; pinned?: boolean; renderItem?: boolean; textFilterField?: string | null; wrap?: boolean;
} = {}) {
  let view!: ResourceViewContextValue;
  function Content() {
    view = useResourceView();
    const catalog = useSearchCatalog({ query, modelMetadata: model, columns, rows: [], resourceView: view,
      search: searchDeclaration, serverGrouping: false, renderItem: options.renderItem, textFilterField: options.textFilterField,
      filterOptions: filters, scalarFacets: [blankStatus] });
    const search = useResourceSearch({ resourceView: view, catalog });
    return <ResourceToolbar search={search} searchDeclaration={searchDeclaration} modelMetadata={model}
      pager={{ total: 0, page: 1, pageSize: 20 }} wrap={options.wrap} />;
  }
  function Preferences() {
    const [preferences, setPreferences] = useState<Record<string, unknown>>({
      [RESOURCE_VIEW_FAVORITES_PREFERENCES_KEY]: { version: 1, models: { "notes.Note": options.pinned ? [
        { id: "favorite:recent", label: "Recent", pinned: true, filter: { title: { iContains: "recent" } } },
      ] : [] } },
    });
    return <AppRuntimeProvider runtime={{ containers: options.containers ?? containersFromChildren(RESOURCE_CONTAINERS, {}),
      containerScope: { apps: [], routes: options.route ? [options.route] : [], perspective: null },
      userPreferences: { available: true, preferences, patchPreferences: async (patch) => setPreferences((current) => patch(current)) } }}>
      <ResourceViewProvider resource="notes.Note" scope="local"><Content /></ResourceViewProvider>
    </AppRuntimeProvider>;
  }
  const rendered = render(<ModelMetadataProvider metadata={metadata}><Preferences /></ModelMetadataProvider>);
  return { ...rendered, get view() { return view; } };
}

test("the reference row is reachable through a list declaration alone, with the collapsed trigger last", () => {
  render(<RowsListView scope="local" query={query} rows={[]} columns={columns} filterOptions={filters} search={declaration} />);
  const row = within(screen.getByRole("toolbar", { name: "Search shortcuts" }));
  expect(row.getAllByRole("searchbox").map((input) => input.getAttribute("aria-label"))).toEqual(["Title", "Filed by"]);
  expect(row.getAllByRole("combobox").map((input) => input.getAttribute("aria-label"))).toEqual(["Priority", "Status"]);
  expect(row.getAllByRole("button").map((button) => button.getAttribute("aria-label") ?? button.textContent)).toEqual([
    "Submitted", "Need by", "Duration", "My records", "Search options",
  ]);
  expect(screen.queryByRole("combobox", { name: "Filter records" })).toBeNull();
  fireEvent.click(row.getByRole("button", { name: "Search options" }));
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBeTruthy();
});

test("a toggle's pressed state and the box chip are the same provider fact in both directions", () => {
  const f = fixture();
  const toggle = screen.getByRole("button", { name: "My records" });
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(toggle);
  expect(f.view.state.filter.owner).toEqual({ exact: "viewer" });
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Search options" }).textContent).toContain("1");
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove My records" }));
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  expect(f.view.state.filter).toEqual({});
  fireEvent.click(within(screen.getByRole("dialog", { name: "Search options" })).getByRole("button", { name: "My records" }));
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
});

test("per-field text shortcuts add text suggestions, debounce provider commands, and reflect chip removal", async () => {
  const f = fixture({ box: true, shortcuts: [{ kind: "text", field: "requester.display_name" }] }, { textFilterField: null });
  const input = screen.getByRole("searchbox", { name: "Filed by" });
  fireEvent.change(input, { target: { value: "Lee" } });
  await waitFor(() => expect(f.view.state.filter["requester.display_name"]).toEqual({ iContains: "Lee" }));
  expect(screen.getByRole("button", { name: "Remove Filed by: Lee" })).toBeTruthy();
  fireEvent.input(screen.getByRole("combobox", { name: "Filter records" }), { target: { value: "review" }, inputType: "insertText" });
  const suggestions = await screen.findAllByRole("option");
  expect(suggestions.map((option) => option.textContent)).toEqual(["Search Filed by for: review"]);
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Filter records" }), { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("option")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Remove Filed by: Lee" }));
  expect((input as HTMLInputElement).value).toBe("");
});

async function select(label: string, value: string) {
  fireEvent.click(screen.getByRole("combobox", { name: label }));
  const option = await screen.findByRole("option", { name: value });
  fireEvent.pointerDown(option, { pointerType: "mouse" }); fireEvent.click(option);
}

test("facet shortcuts select multiple values and the catalog blank bucket, reflected in box chips", async () => {
  const f = fixture({ box: true, shortcuts: [{ kind: "facet", field: "status" }] });
  await select("Status", "Open");
  let option = await screen.findByRole("option", { name: "Closed" });
  fireEvent.pointerDown(option, { pointerType: "mouse" }); fireEvent.click(option);
  expect(f.view.state.filter.status).toEqual({ inList: ["open", "closed"] });
  expect(screen.getByRole("combobox", { name: "Status" }).textContent).toContain("Open, Closed");
  expect(screen.getByRole("button", { name: "Remove Status: Open or Closed" })).toBeTruthy();
  option = await screen.findByRole("option", { name: "Not given" });
  fireEvent.pointerDown(option, { pointerType: "mouse" }); fireEvent.click(option);
  expect(f.view.state.filter.status).toEqual({ isNull: true });
  option = await screen.findByRole("option", { name: "Closed" });
  fireEvent.pointerDown(option, { pointerType: "mouse" }); fireEvent.click(option);
  expect(f.view.state.filter.status).toEqual({ exact: "closed" });
  fireEvent.keyDown(screen.getByRole("option", { name: "Closed" }), { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "Remove Status: Closed" }));
  expect(screen.getByRole("combobox", { name: "Status" }).textContent).toBe("Status");
});

test("a single-select facet still uses the shared facet command", async () => {
  const f = fixture({ shortcuts: [{ kind: "facet", field: "status", multiple: false }] });
  await select("Status", "Not given");
  expect(f.view.state.filter.status).toEqual({ isNull: true });
});

test("a clause shortcut edits and clears the same field lookup, including Not given", async () => {
  const f = fixture({ box: true, shortcuts: [{ kind: "clause", field: "duration" }] });
  fireEvent.click(screen.getByRole("button", { name: "Duration" }));
  await select("Filter operator", ">=");
  fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(f.view.state.filter.duration).toEqual({ gte: 4 });
  expect(screen.getByRole("button", { name: "Remove Duration >= 4" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Duration" }));
  expect((screen.getByLabelText("Filter value") as HTMLInputElement).value).toBe("4");
  fireEvent.click(screen.getByRole("button", { name: "Not given" }));
  expect(f.view.state.filter.duration).toEqual({ isNull: true });
  fireEvent.click(within(screen.getByRole("dialog", { name: "Duration" })).getByRole("button", { name: "Clear" }));
  expect(f.view.state.filter).toEqual({});
});

test("the group shortcut hosts the shared stack panel and box chips remove its levels", () => {
  const f = fixture({ box: true, shortcuts: [{ kind: "group" }] });
  fireEvent.click(screen.getByRole("button", { name: "Group by" }));
  fireEvent.click(screen.getByRole("button", { name: "Status" }));
  expect(f.view.state.groupStack).toEqual([{ field: "status" }]);
  expect(screen.getByRole("button", { name: /Group by: Status/ })).toBeTruthy();
  fireEvent.keyDown(screen.getByRole("button", { name: "Clear grouping" }), { key: "Escape" });
  fireEvent.click(within(screen.getByRole("toolbar", { name: "Active search" })).getByRole("button", { name: "Remove Status" }));
  expect(f.view.state.groupStack).toEqual([]);
});

test("pinned favorites trail declared shortcuts and never collapse an otherwise box-only list", () => {
  fixture({}, { pinned: true });
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBeTruthy();
  const favorite = screen.getByRole("button", { name: "Recent" });
  fireEvent.click(favorite);
  expect(favorite.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Remove Recent" })).toBeTruthy();
  cleanup();
  fixture({ shortcuts: [{ kind: "toggle", id: "mine" }] }, { pinned: true });
  const row = within(screen.getByRole("toolbar", { name: "Search shortcuts" }));
  expect(row.getAllByRole("button").map((button) => button.textContent)).toEqual(["My records", "Recent", ""]);
});

test("a model contribution interleaves by sequence and only plus route narrowing reaches page shortcuts", () => {
  const composed = containersFromChildren(RESOURCE_CONTAINERS, { "notes.Note#search": {
    "extension.mine": { sequence: 15, content: { kind: "toggle", id: "mine" } },
  } });
  const search: ListSearchDeclaration = { shortcuts: [
    { kind: "text", field: "title", sequence: 10 }, { kind: "text", field: "requester.display_name", sequence: 20 },
  ] };
  composed.rules = { "notes.Note#search": [{ layer: "deployment", rank: 1, exempt: [],
    only: ["page.text.title", "extension.mine"], when: { route: "extension.queue" } }] };
  fixture(search, { containers: composed });
  const row = screen.getByRole("toolbar", { name: "Search shortcuts" });
  expect([...row.querySelectorAll('input[type="search"], button')].map((item) => item.getAttribute("aria-label") ?? item.textContent))
    .toEqual(["Title", "My records", "Filed by", "Search options"]);
  cleanup();
  fixture(search, { containers: composed, route: "extension.queue" });
  expect(screen.queryByRole("searchbox", { name: "Filed by" })).toBeNull();
  expect(screen.getByRole("searchbox", { name: "Title" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Search options" })).toBeTruthy();
});

test("unknown targets, nonfilterable fields, text without iContains and duplicate page ids fail with identity", () => {
  expect(() => fixture({ shortcuts: [{ kind: "toggle", id: "missing" }] })).toThrow(/page.toggle.missing.*unknown toggle id/);
  expect(() => fixture({ shortcuts: [{ kind: "clause", field: "missing" }] })).toThrow(/page.clause.missing.*not filterable/);
  expect(() => fixture({ shortcuts: [{ kind: "text", field: "duration" }] })).toThrow(/page.text.duration.*iContains/);
  expect(() => fixture({ shortcuts: [{ kind: "facet", field: "owner" }] })).toThrow(/page.facet.owner.*facet catalog/);
  expect(() => fixture({ shortcuts: [{ kind: "group" }] }, { renderItem: true })).toThrow(/page.group.*renderItem/);
  expect(() => fixture({ shortcuts: [{ kind: "text", field: "title" }, { kind: "text", field: "title" }] })).toThrow(/Duplicate.*page.text.title/);
  expect(() => fixture({}, { containers: containersFromChildren(RESOURCE_CONTAINERS, { "resource#search": {
    "extension.missing": { content: { kind: "toggle", id: "missing" } },
  } }) })).toThrow(/extension.missing.*unknown toggle id/);
  expect(() => fixture({}, { containers: containersFromChildren(RESOURCE_CONTAINERS, { "resource#search": {
    "extension.field": { content: { kind: "clause", field: "missing" } },
  } }) })).toThrow(/extension.field.*missing.*not filterable/);
  // @ts-expect-error The full box remains reachable; false is not a declaration mode.
  expect(() => pageSearchShortcuts({ box: false })).toThrow(/box must be true or "collapsed"/);
});

test("toolbar arrow navigation roves across facet, toggle and the trailing box trigger", async () => {
  fixture({ shortcuts: [{ kind: "facet", field: "status" }, { kind: "toggle", id: "mine" }] });
  const facet = screen.getByRole("combobox", { name: "Status" });
  facet.focus();
  fireEvent.keyDown(facet, { key: "ArrowRight" });
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "My records" })));
  fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Search options" })));
});

test("below 36rem container width shortcuts hide and even an explicitly full box becomes its reachable badge", () => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 560, height: 80, top: 0, left: 0,
    bottom: 80, right: 560, x: 0, y: 0, toJSON: () => ({}) });
  fixture({ ...declaration, box: true });
  expect(screen.queryByRole("searchbox")).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Filter records" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBeTruthy();
});

test("the search area flexes only for the full box: shortcuts take a row, and a collapsed trigger keeps its natural width", () => {
  const area = () => screen.getByRole("toolbar", { name: "Search shortcuts" });
  const host = () => screen.getByRole("button", { name: "Search options" }).parentElement!;
  fixture({});
  expect(area().getAttribute("data-search-layout")).toBe("box");
  expect(area().parentElement?.className).toContain("resource-toolbar-query");
  expect(host().className).toContain("flex-1");
  cleanup();
  fixture();
  expect(area().getAttribute("data-search-layout")).toBe("shortcuts");
  cleanup();
  fixture({ box: "collapsed" });
  expect(area().getAttribute("data-search-layout")).toBe("trigger");
  expect(host().className).toContain("shrink-0");
  expect(host().className).not.toContain("flex-1");
  cleanup();
  // A narrow wrapping pane: the trigger alone joins the actions and pager row, even when shortcuts are declared.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 293, 80));
  for (const searchDeclaration of [{}, declaration]) {
    fixture(searchDeclaration, { wrap: true });
    expect(area().closest("section")?.className).toContain("resource-toolbar-wrap");
    expect(area().getAttribute("data-search-layout")).toBe("trigger");
    expect(host().className).toContain("shrink-0");
    cleanup();
  }
});

test("a contributed shortcut alone chooses the collapsed default; an empty declaration chooses the full box", () => {
  fixture({});
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBeTruthy();
  cleanup();
  fixture({}, { containers: containersFromChildren(RESOURCE_CONTAINERS, { "resource#search": {
    "extension.mine": { content: { kind: "toggle", id: "mine" } },
  } }) });
  expect(screen.getByRole("button", { name: "My records" })).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Filter records" })).toBeNull();
});

test("a production list renders and keeps working after dropping a filter and its toggle, reporting once per mount", async () => {
  vi.mocked(developmentMode).mockReturnValue(false);
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  function list() {
    return <RowsListView scope="local" query={query} rows={[{ id: "visible", title: "Visible record", owner: "viewer" }]} columns={columns}
      filterOptions={[...filters, { id: "setup", label: "Accepted", filter: { "stage.name": { exact: "Accepted" } } }]}
      search={{ shortcuts: [{ kind: "toggle", id: "setup", label: "Accepted" }, { kind: "toggle", id: "mine" }] }} />;
  }
  const rendered = render(list());
  expect(await screen.findByText("Visible record")).toBeTruthy();
  expect(screen.getByRole("toolbar", { name: "Search shortcuts" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Accepted" })).toBeNull();
  const validToggle = screen.getByRole("button", { name: "My records" });
  fireEvent.click(validToggle);
  expect(validToggle.getAttribute("aria-pressed")).toBe("true");
  rendered.rerender(list());
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  expect(screen.getByRole("combobox", { name: "Filter records" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Accepted" })).toBeNull();
  expect(error).toHaveBeenCalledTimes(2);
  expect(error).toHaveBeenCalledWith('Search filter option "setup": filter.stage.name: unknown or non-filterable field.');
  expect(error).toHaveBeenCalledWith('Search shortcut "page.toggle.setup": unknown toggle id "setup".');
  rendered.unmount();
  render(list());
  expect(error).toHaveBeenCalledTimes(4);
});

test("standalone shortcut controls drop unavailable targets once in production and throw with identity in development", () => {
  const search = searchFixture();
  const shortcuts = pageSearchShortcuts({ shortcuts: [{ kind: "toggle", id: "setup", label: "Accepted" }, { kind: "text", field: "title" }] });
  expect(() => validateSearchShortcutCatalog(shortcuts, search.catalog)).toThrow(/page.toggle.setup.*unknown toggle id/);
  vi.mocked(developmentMode).mockReturnValue(false);
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const rendered = render(<SearchControls search={search} shortcuts={shortcuts} />);
  expect(screen.queryByRole("button", { name: "Accepted" })).toBeNull();
  expect(screen.getByRole("searchbox", { name: "Title" })).toBeTruthy();
  rendered.rerender(<SearchControls search={{ ...search, catalog: { ...search.catalog } }} shortcuts={[...shortcuts]} />);
  expect(error).toHaveBeenCalledExactlyOnceWith('Search shortcut "page.toggle.setup": unknown toggle id "setup".');
});

test("malformed shortcuts, duplicate ids, box false and renderItem grouping still fail fast in production", () => {
  vi.mocked(developmentMode).mockReturnValue(false);
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  for (const content of [null, { kind: "unknown" }, { kind: "toggle" }, { kind: "text" },
    { kind: "group", sequence: Infinity }, { kind: "facet", field: "status", multiple: "yes" }]) {
    expect(() => validateSearchShortcutCatalog([
      // @ts-expect-error Malformed runtime declarations must fail at the boundary.
      { id: "extension.malformed", owner: "extension", address: "resource#search", content },
    ], searchFixture().catalog)).toThrow(/extension.malformed/);
  }
  expect(() => pageSearchShortcuts({ shortcuts: [{ kind: "toggle", id: "setup" }, { kind: "toggle", id: "setup" }] })).toThrow(/Duplicate.*page.toggle.setup/);
  // @ts-expect-error The box cannot be disabled by a declaration.
  expect(() => pageSearchShortcuts({ box: false })).toThrow(/box must be true or "collapsed"/);
  expect(() => validateSearchShortcutCatalog(pageSearchShortcuts({ shortcuts: [{ kind: "group" }] }),
    searchFixture().catalog, { renderItem: true })).toThrow(/page.group.*renderItem/);
  expect(error).not.toHaveBeenCalled();
});
