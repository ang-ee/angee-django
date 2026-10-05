// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { GroupSpec } from "@angee/metadata";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { GroupStackPanel } from "./GroupStackPanel";
import { searchFixture } from "./search-fixture.test-support";
import { useResourceSearch } from "./use-resource-search";

afterEach(cleanup);
const project = { id: "project", label: "Project", group: { field: "project" } };
const status = { id: "status", label: "Status", group: { field: "status" } };
const created = { id: "created", label: "Created", group: { field: "created", granularity: "day" },
  type: "date" as const, granularities: ["year", "month", "day", "hour", "day_of_week"] };
const catalog = searchFixture({ catalog: { groups: [project, status, created], curatedGroups: [project, status, created] } }).catalog;

function panel(groupStack: readonly GroupSpec[], maxGroupDepth?: number) {
  function Content() {
    const resourceView = useResourceView();
    const search = useResourceSearch({ resourceView, catalog, groupingEnabled: true, maxGroupDepth });
    return <GroupStackPanel search={search} />;
  }
  return render(<ResourceViewProvider scope="local" initialState={{ groupStack }}><Content /></ResourceViewProvider>);
}

function levels() {
  return within(screen.getByRole("list", { name: "Levels" })).queryAllByRole("listitem")
    .map((level) => level.getAttribute("aria-label"));
}

async function select(label: string, choice: string) {
  fireEvent.click(screen.getByRole("combobox", { name: label }));
  const option = await screen.findByRole("option", { name: choice });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
}

test("levels render in stack order and end moves are absent", () => {
  panel([status.group, { field: "created", granularity: "month" }, project.group]);
  expect(levels()).toEqual(["Status", "Created · Month", "Project"]);
  expect(screen.queryByRole("button", { name: "Move up Status" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Move down Project" })).toBeNull();
});

test("Move up and Move down reorder and keep focus on the moved level even at either end", () => {
  panel([project.group, status.group, created.group]);
  const moveUp = screen.getByRole("button", { name: "Move up Status" });
  moveUp.focus();
  fireEvent.click(moveUp);
  expect(levels()).toEqual(["Status", "Project", "Created · Day"]);
  expect(document.activeElement).toBe(screen.getByRole("listitem", { name: "Status" }));
  fireEvent.click(screen.getByRole("button", { name: "Move down Status" }));
  expect(levels()).toEqual(["Project", "Status", "Created · Day"]);
  expect(document.activeElement).toBe(screen.getByRole("listitem", { name: "Status" }));
  fireEvent.click(screen.getByRole("button", { name: "Move down Status" }));
  expect(levels()).toEqual(["Project", "Created · Day", "Status"]);
  expect(document.activeElement).toBe(screen.getByRole("listitem", { name: "Status" }));
});

test("Remove removes only its level, including when another level has the same field", () => {
  panel([{ field: "created", granularity: "month" }, status.group, { field: "created", granularity: "year" }]);
  fireEvent.click(screen.getByRole("button", { name: "Remove Created · Month" }));
  expect(levels()).toEqual(["Status", "Created · Year"]);
  fireEvent.click(screen.getByRole("button", { name: "Remove Status" }));
  expect(levels()).toEqual(["Created · Year"]);
});

test("granularity changes its level in place without changing the other levels", async () => {
  panel([project.group, { field: "created", granularity: "month" }, status.group]);
  await select("Granularity for Created · Month", "Year");
  expect(levels()).toEqual(["Project", "Created · Year", "Status"]);
  expect(screen.queryByRole("combobox", { name: /Granularity for (Status|Project)/ })).toBeNull();
});

test("curated axes add their default granularity and primary granularities add a specific level", () => {
  panel([]);
  fireEvent.click(screen.getByRole("button", { name: "Created" }));
  fireEvent.click(screen.getByRole("button", { name: "Add Created · Month" }));
  expect(levels()).toEqual(["Created · Day", "Created · Month"]);
  expect(screen.getByRole("button", { name: "Add Created · Month" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "More granularities for Created" }));
  fireEvent.click(screen.getByRole("button", { name: "Add Created · Day of week" }));
  expect(levels()).toEqual(["Created · Day", "Created · Month", "Created · Day of week"]);
});

test("More axes offers the complete catalog and adds the chosen granularity", async () => {
  panel([status.group]);
  fireEvent.click(screen.getByRole("button", { name: "More axes…" }));
  await select("Group axis", "Created");
  await select("Group granularity", "Hour");
  fireEvent.click(screen.getByRole("button", { name: "Add level" }));
  expect(levels()).toEqual(["Status", "Created · Hour"]);
});

test.each([1, 2])("depth cap %s disables every Add path until a level is removed", async (maxGroupDepth) => {
  panel(maxGroupDepth === 1 ? [] : [project.group], maxGroupDepth);
  fireEvent.click(screen.getByRole("button", { name: "More axes…" }));
  await select("Group axis", "Status");
  fireEvent.click(screen.getByRole("button", { name: "Status" }));
  expect(screen.getByRole("button", { name: "Add level" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "More axes…" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Created" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Add Created · Month" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Add Created · Month" }));
  expect(levels()).toEqual(maxGroupDepth === 1 ? ["Status"] : ["Project", "Status"]);
  fireEvent.click(screen.getByRole("button", { name: "Remove Status" }));
  expect(screen.getByRole("button", { name: "Add level" }).hasAttribute("disabled")).toBe(false);
  expect(screen.getByRole("button", { name: "Created" }).hasAttribute("disabled")).toBe(false);
});

test("Clear grouping clears the stack and leaves Add available", () => {
  panel([project.group, status.group], 2);
  fireEvent.click(screen.getByRole("button", { name: "Clear grouping" }));
  expect(levels()).toEqual([]);
  expect(screen.queryByRole("button", { name: "Clear grouping" })).toBeNull();
  expect(screen.getByRole("button", { name: "More axes…" }).hasAttribute("disabled")).toBe(false);
});
