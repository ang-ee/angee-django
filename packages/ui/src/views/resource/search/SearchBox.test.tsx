// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SearchBox } from "./SearchBox";
import { searchFixture } from "./search-fixture.test-support";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { useResourceSearch } from "./use-resource-search";

// The panel is a popup, so the viewport (not the box's own width) decides its columns.
const viewport = vi.hoisted(() => ({ roomy: true }));
vi.mock("../../../lib/use-media-query", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../../lib/use-media-query")>(),
  useMediaQuery: () => viewport.roomy,
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); viewport.roomy = true; });
const status = { id: "status", label: "Status", group: { field: "status" } };
const created = { id: "created", label: "Created", group: { field: "created", granularity: "month" } };

test("typing offers per-field searches before a facet match and Enter applies the first", async () => {
  const search = searchFixture({ catalog: { text: [{ field: "title", label: "Title" }, { field: "description", label: "Description" }],
    facets: [{ field: "status", label: "Status", source: "scalar", options: [
      { id: "abandoned", label: "Abandoned", value: "abandoned", filter: { status: { exact: "abandoned" } } },
    ] }] } });
  render(<SearchBox search={search} />);
  const input = screen.getByRole("combobox", { name: "Filter records" });
  fireEvent.input(input, { target: { value: "ab" }, inputType: "insertText" });
  await waitFor(() => expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "Search Title for: ab", "Search Description for: ab", "Status: Abandoned",
  ]));
  fireEvent.keyDown(input, { key: "Enter" });
  expect(search.setText).toHaveBeenCalledWith("ab", "title");
  expect((input as HTMLInputElement).value).toBe("");
});

test("each active kind has one chip and its remove calls the model's clear", () => {
  const search = searchFixture({ active: [
    { id: "filter:mine", kind: "filter", label: "Mine" },
    { id: "facet:status", kind: "facet", field: "status", label: "Status", options: [
      { id: "open", label: "Open", value: "open", filter: { status: { exact: "open" } } },
      { id: "closed", label: "Closed", value: "closed", filter: { status: { exact: "closed" } } },
    ] },
    { id: "text:title", kind: "text", field: "title", label: "Title", value: "ab" },
    { id: "clause:amount:gte", kind: "clause", label: "Amount >= 2" },
    { id: "group:0", kind: "group", index: 0, level: status.group, label: "Status" },
    { id: "favorite:recent", kind: "favorite", label: "Recent" },
  ] });
  render(<SearchBox search={search} />);
  const chips = within(screen.getByRole("toolbar", { name: "Active search" })).getAllByRole("button");
  expect(chips).toHaveLength(6);
  expect(chips[1]?.parentElement?.textContent).toContain("Status: Open or Closed");
  chips.forEach((chip, index) => {
    fireEvent.click(chip);
    expect(search.clear).toHaveBeenLastCalledWith(search.active[index]?.id);
  });
  expect(search.clear).toHaveBeenCalledTimes(6);
});

test("Backspace removes the last chip and Delete removes the focused chip without clearing others on Escape", async () => {
  const catalog = searchFixture({ catalog: { filters: [
    { id: "open", label: "Open", filter: { status: { exact: "open" } } },
  ], groups: [status, created] } }).catalog;
  function Content() {
    const search = useResourceSearch({ catalog, resourceView: useResourceView(), groupingEnabled: true });
    return <SearchBox search={search} />;
  }
  render(<ResourceViewProvider scope="local" initialState={{ filter: { status: { exact: "open" } }, groupStack: [status.group, created.group] }}>
    <Content />
  </ResourceViewProvider>);
  const input = screen.getByRole("combobox", { name: "Filter records" });
  input.focus();
  fireEvent.keyDown(input, { key: "Backspace" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Remove Created · Month" })).toBeNull());
  fireEvent.keyDown(input, { key: "Escape" });
  fireEvent.keyDown(input, { key: "ArrowLeft" });
  fireEvent.keyDown(document.activeElement!, { key: "Delete" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Remove Status" })).toBeNull());
  expect(screen.getByRole("button", { name: "Remove Open" })).toBeTruthy();
});

test("collapsed mode counts all items and opens the chips, input and panel with focus", async () => {
  const search = searchFixture({ active: [
    { id: "text:title", kind: "text", field: "title", label: "Title", value: "ab" },
    { id: "group:0", kind: "group", index: 0, level: status.group, label: "Status" },
  ], groupingEnabled: true, groupStack: [status.group], catalog: { groups: [status] } });
  render(<SearchBox search={search} box="collapsed" />);
  const trigger = screen.getByRole("button", { name: "Search options" });
  expect(trigger.textContent).toBe("2");
  expect(screen.queryByRole("combobox", { name: "Filter records" })).toBeNull();
  fireEvent.click(trigger);
  const input = await screen.findByRole("combobox", { name: "Filter records" });
  expect(screen.getByRole("button", { name: "Remove Title: ab" })).toBeTruthy();
  await waitFor(() => expect(document.activeElement).toBe(input));
  fireEvent.keyDown(input, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Filter records" })).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});

test.each([false, true])("the panel reaches every catalog choice and stacks its columns on a narrow viewport (roomy=%s)", async (roomy) => {
  viewport.roomy = roomy;
  const search = searchFixture({ groupingEnabled: true, saveFavorite: vi.fn(), catalog: {
    filters: [{ id: "mine", label: "Mine", filter: { owner: { exact: "viewer" } } }],
    facets: [{ field: "status", label: "Status", source: "scalar", options: [
      { id: "blank", label: "Not given", filter: { status: { isNull: true } } },
    ] }],
    groups: [status, created], curatedGroups: [status], fields: [
      { id: "amount", label: "Amount", type: "number", operators: ["gte"] },
      { id: "hidden", label: "Hidden field", type: "text", operators: ["exact"] },
    ], favorites: [{ id: "favorite:recent", label: "Recent" }],
  } });
  render(<SearchBox search={search} />);
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  const panel = screen.getByLabelText("Search options", { selector: "[role=dialog]" });
  expect(panel.className).toContain(roomy ? "grid-cols-3" : "grid-cols-1");
  expect(within(panel).getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(expect.arrayContaining(["Filters", "Group by", "Favorites"]));
  fireEvent.click(within(panel).getByRole("button", { name: "Mine" }));
  expect(search.toggleFilter).toHaveBeenCalledWith("mine");
  fireEvent.click(within(panel).getByRole("button", { name: "Not given" }));
  expect(search.toggleFacetOption).toHaveBeenCalledWith("status", "blank");
  fireEvent.click(within(panel).getByRole("button", { name: "Recent" }));
  expect(search.applyFavorite).toHaveBeenCalledWith("favorite:recent");
  fireEvent.click(within(panel).getByRole("button", { name: "More axes…" }));
  fireEvent.click(within(panel).getByRole("combobox", { name: "Group axis" }));
  expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(expect.arrayContaining(["Status", "Created"]));
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Group axis" }), { key: "Escape" });
  fireEvent.click(within(panel).getByRole("button", { name: "Add custom filter" }));
  fireEvent.click(within(panel).getByRole("combobox", { name: "Filter field" }));
  expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(expect.arrayContaining(["Amount", "Hidden field"]));
});
