// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { searchFixture } from "./search/search-fixture.test-support";
import { ResourceListFrame } from "./ResourceListFrame";
import { listChromeState } from "./resource-view-types";

afterEach(cleanup);

test("list chrome composes heading copy with the live count", () => {
  render(<ResourceListFrame toolbar={{ search: searchFixture(), pager: { total: 7, page: 1, pageSize: 20 } }}
    heading={{ label: "Requests", hint: "Filed through the public form", audience: "Managers only" }}>
    <p>Rows</p>
  </ResourceListFrame>);
  const heading = screen.getByRole("heading", { name: "Requests" });
  expect(heading.parentElement?.textContent).toContain("· 7");
  expect(heading.parentElement?.textContent).toContain("Filed through the public form");
  expect(heading.parentElement?.textContent).toContain("Managers only");
});

test("embedded chrome is compact until the collection outgrows its page", () => {
  const settled = { page: 1, pageSize: 20, queryDirty: false };
  expect(listChromeState("embedded", undefined, { ...settled, total: 3 })).toEqual({
    compact: true, viewSwitcher: false, columnChooser: false, search: false, pager: false,
  });
  expect(listChromeState("embedded", undefined, { ...settled, total: 21 })).toMatchObject({ search: true, pager: true });
  expect(listChromeState("embedded", undefined, { ...settled, total: undefined, hasNext: true })).toMatchObject({ search: true, pager: true });
  // A narrowed query or a later page keeps the controls that lead back.
  expect(listChromeState("embedded", undefined, { ...settled, total: 2, queryDirty: true })).toMatchObject({ search: true, pager: false });
  expect(listChromeState("embedded", undefined, { ...settled, total: 3, page: 2 })).toMatchObject({ search: true, pager: true });
  expect(listChromeState("page", undefined, { ...settled, total: 3 })).toEqual({
    compact: false, viewSwitcher: true, columnChooser: true, search: true, pager: true,
  });
});

test("declared chrome overrides every presentation default", () => {
  const collection = { total: 3, page: 1, pageSize: 20, queryDirty: false };
  expect(listChromeState("embedded", { search: true, viewSwitcher: true, columnChooser: true }, collection))
    .toMatchObject({ compact: true, search: true, viewSwitcher: true, columnChooser: true, pager: false });
  expect(listChromeState("embedded", { pager: false }, { ...collection, total: 40 }))
    .toMatchObject({ search: true, pager: false });
  expect(listChromeState("page", { viewSwitcher: false, pager: false, columnChooser: false, search: false }, collection))
    .toMatchObject({ compact: false, viewSwitcher: false, pager: false, columnChooser: false, search: false });
});

test("a compact heading row carries the count, create and toolbar actions instead of the control band", () => {
  const onCreate = vi.fn();
  render(<ResourceListFrame compact presentation="embedded" heading={{ label: "Addresses" }}
    toolbar={{ search: searchFixture(), pager: { total: 3, page: 1, pageSize: 20 }, onCreate, createLabel: "New address",
      actions: <button type="button">Import</button>, utilityActions: <button type="button">Share</button>,
      chrome: { search: false, pager: false, viewSwitcher: false } }}>
    <p>Rows</p>
  </ResourceListFrame>);
  const heading = screen.getByRole("heading", { name: "Addresses" });
  const row = heading.parentElement!.parentElement!;
  expect(row.textContent).toContain("· 3");
  expect(within(row).getByRole("button", { name: "Import" })).toBeTruthy();
  fireEvent.click(within(row).getByRole("button", { name: "New address" }));
  expect(onCreate).toHaveBeenCalledOnce();
  expect(screen.queryByRole("region", { name: "Data controls" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Share" })).toBeNull();
});

test("a compact list renders its enabled controls inline, below the heading row", () => {
  render(<ResourceListFrame compact presentation="embedded" heading={{ label: "Addresses" }}
    toolbar={{ search: searchFixture(), pager: { total: 40, page: 1, pageSize: 20 }, onCreate: vi.fn(), createLabel: "New address",
      chrome: { search: true, pager: true, viewSwitcher: false } }}>
    <p>Rows</p>
  </ResourceListFrame>);
  const controls = screen.getByRole("region", { name: "Data controls" });
  expect(within(controls).getByRole("button", { name: "Next page" })).toBeTruthy();
  expect(within(controls).queryByRole("button", { name: "New address" })).toBeNull();
  expect(screen.getAllByRole("button", { name: "New address" })).toHaveLength(1);
});

test("a compact list whose host owns the heading keeps its verbs in the toolbar", () => {
  render(<ResourceListFrame compact presentation="embedded" heading={false}
    toolbar={{ search: searchFixture(), pager: { total: 3, page: 1, pageSize: 20 }, onCreate: vi.fn(), createLabel: "New address",
      chrome: { search: false, pager: false, viewSwitcher: false } }}>
    <p>Rows</p>
  </ResourceListFrame>);
  expect(screen.queryByRole("heading")).toBeNull();
  const controls = screen.getByRole("region", { name: "Data controls" });
  expect(within(controls).getByRole("button", { name: "New address" })).toBeTruthy();
});

test("a grouped heading counts groups in their own unit", () => {
  render(<ResourceListFrame compact presentation="embedded" heading={{ label: "Parts" }}
    toolbar={{ search: searchFixture(), pager: { total: 2, page: 1, pageSize: 20 }, pagerTotalUnit: "groups",
      chrome: { search: false, pager: false, viewSwitcher: false } }}>
    <p>Rows</p>
  </ResourceListFrame>);
  expect(screen.getByRole("heading", { name: "Parts" }).parentElement?.textContent).toContain("· 2 groups");
});

test("retains and dims settled rows only while the new request has none", () => {
  const toolbar = { search: searchFixture(), pager: { total: 1, page: 1, pageSize: 20 } };
  const rendered = render(<ResourceListFrame toolbar={toolbar} hasRows><p>Previous row</p></ResourceListFrame>);
  rendered.rerender(<ResourceListFrame toolbar={toolbar} fetching hasRows={false}><p>Next row</p></ResourceListFrame>);
  expect(screen.getByText("Previous row")).toBeTruthy();
  expect(screen.queryByText("Next row")).toBeNull();
  expect(screen.getByText("Previous row").parentElement?.className).toContain("opacity-50");
  rendered.rerender(<ResourceListFrame toolbar={toolbar} hasRows><p>Next row</p></ResourceListFrame>);
  expect(screen.getByText("Next row")).toBeTruthy();
});

test("background refetch keeps current rows interactive and undimmed", () => {
  const toolbar = { search: searchFixture(), pager: { total: 1, page: 1, pageSize: 20 } };
  const rendered = render(<ResourceListFrame toolbar={toolbar} hasRows><button type="button">Open row</button></ResourceListFrame>);
  rendered.rerender(<ResourceListFrame toolbar={toolbar} fetching hasRows><button type="button">Open row</button></ResourceListFrame>);
  const row = screen.getByRole("button", { name: "Open row" });
  expect(row.parentElement?.className).not.toContain("opacity-50");
  expect(row.parentElement?.className).not.toContain("pointer-events-none");
  expect(row.parentElement?.getAttribute("aria-busy")).toBe("true");
});

test.each(["resource-table-scroll", "resource-board-scroll"])(
  "%s stays a direct frame child for page and embedded layout rules",
  (scrollClass) => {
    const toolbar = { search: searchFixture(), pager: { total: 1, page: 1, pageSize: 20 } };
    const rendered = render(<ResourceListFrame toolbar={toolbar} presentation="page">
      <div className={scrollClass}>Rows</div>
    </ResourceListFrame>);
    expect(screen.getByText("Rows").parentElement?.classList.contains("resource-list-frame")).toBe(true);
    rendered.rerender(<ResourceListFrame toolbar={toolbar} presentation="embedded">
      <div className={scrollClass}>Rows</div>
    </ResourceListFrame>);
    expect(screen.getByText("Rows").parentElement?.getAttribute("data-resource-presentation")).toBe("embedded");
  },
);
