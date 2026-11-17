// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { SearchBox } from "./SearchBox";
import { searchFixture } from "./search-fixture.test-support";

// The grouped facet reads its options' related records through the relation
// options owner; stand it in with the rows that owner projects.
const related = vi.hoisted(() => ({ calls: [] as unknown[] }));
vi.mock("../../relation/relation-options", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../relation/relation-options")>(),
  useRelationOptions: (_relation: unknown, config: unknown) => {
    related.calls.push(config);
    return {
      list: { fetching: false, refetch: () => undefined },
      rows: [],
      options: [
        { value: "tag-partner", label: "Partner", group: "Relationship", groupOrder: 2 },
        { value: "tag-enterprise", label: "Enterprise", group: "Segment", groupOrder: 1 },
        { value: "tag-loose", label: "Loose" },
      ],
    };
  },
}));

afterEach(() => { cleanup(); related.calls = []; });

const option = (value: string, label: string) => ({ id: `tags:"${value}"`, label, value, filter: { tags: { exact: value } } });

test("a relation facet declaring an option group lists its options under the related records' headings", () => {
  const search = searchFixture({ catalog: { facets: [{
    field: "tags", label: "Tags", source: "relation",
    relation: { resource: "tags.Tag", labelField: "name", canCreate: false },
    optionGroupField: "category.name", optionGroupOrderField: "category.position",
    options: [option("tag-loose", "Loose"), option("tag-partner", "Partner"), option("tag-enterprise", "Enterprise")],
  }] } });
  render(<SearchBox search={search} />);
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  const segment = screen.getByRole("group", { name: "Segment" });
  expect(within(segment).getByRole("button", { name: "Enterprise" })).toBeTruthy();
  const section = screen.getByRole("heading", { name: "Tags" }).parentElement!;
  expect(within(section).getAllByRole("group").map((group) => group.getAttribute("aria-label")))
    .toEqual(["Segment", "Relationship", "Other"]);
  expect(related.calls).toContainEqual(expect.objectContaining({
    filters: [{ field: "id", operator: "in", value: ["tag-loose", "tag-partner", "tag-enterprise"] }],
    pageSize: 3, optionGroupField: "category.name", optionGroupOrderField: "category.position",
  }));
  fireEvent.click(within(segment).getByRole("button", { name: "Enterprise" }));
  expect(search.toggleFacetOption).toHaveBeenCalledWith("tags", 'tags:"tag-enterprise"');
});

test("a facet without an option group lists its options flat and reads nothing", () => {
  const search = searchFixture({ catalog: { facets: [{
    field: "status", label: "Status", source: "scalar",
    options: [{ id: "open", label: "Open", value: "open", filter: { status: { exact: "open" } } }],
  }] } });
  render(<SearchBox search={search} />);
  fireEvent.click(screen.getByRole("button", { name: "Search options" }));
  expect(screen.getByRole("button", { name: "Open" })).toBeTruthy();
  expect(screen.queryByRole("group", { name: "Other" })).toBeNull();
  expect(related.calls).toEqual([]);
});
