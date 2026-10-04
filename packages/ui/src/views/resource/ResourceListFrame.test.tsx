// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { searchFixture } from "./search/search-fixture.test-support";
import { ResourceListFrame } from "./ResourceListFrame";

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
