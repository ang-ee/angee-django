// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { Row } from "@angee/metadata";
import { afterEach, expect, test, vi } from "vitest";

import { GroupedListBody } from "./GroupedList";
import { ResourceViewProvider, useResourceView } from "./resource-view-context";
import { estimateGroupedItemSize, type GroupedListItem } from "./resource-view-list-body";
import type { ColumnDescriptor } from "../page";

afterEach(cleanup);

const defaultColumns: readonly ColumnDescriptor<Row>[] = [{ field: "title", header: "Title" }];

function Harness({ pending = false, actions = false, columns = defaultColumns, onPageChange, onToggle }: {
  pending?: boolean;
  actions?: boolean;
  columns?: readonly ColumnDescriptor<Row>[];
  onPageChange: (key: string, page: number) => void;
  onToggle: (key: string) => void;
}): React.ReactElement {
  const resourceView = useResourceView();
  const tableColumns = columns.map((column) => ({ id: column.field, accessorKey: column.field, header: column.field }));
  const table = useReactTable<Row>({ data: [], columns: tableColumns, getCoreRowModel: getCoreRowModel() });
  const tableScrollRef = React.useRef<HTMLDivElement>(null);
  const listItems: GroupedListItem<Row>[] = [
    {
      kind: "groupHeader", bucketKey: "january", depth: 0, label: "January",
      count: 45, expandable: true, expanded: true,
      bucket: { key: { month: "January" }, count: 45, sum: { amount: 30 } },
      pager: { pageKey: "january", page: 2, pageSize: 20, total: 45, unit: "records", pending },
    },
    { kind: "status", itemKey: "body", depth: 0, message: "Group body", tone: "muted" },
  ];
  const rowVirtualizer = useVirtualizer({
    count: listItems.length,
    getScrollElement: () => tableScrollRef.current,
    estimateSize: (index) => estimateGroupedItemSize(listItems[index]),
  });
  return (
    <GroupedListBody
      columns={columns} table={table} tableColumns={tableColumns} visibleColumnCount={columns.length}
      resourceView={resourceView} listItems={listItems} tableScrollRef={tableScrollRef}
      rowVirtualizer={rowVirtualizer} footerAggregate={null} expandedKeys={new Set(["january"])}
      toggleGroup={onToggle} setScopePage={onPageChange} selectedIds={new Set()} interactive
      renderRowActions={actions ? () => null : undefined} emptyContent="Empty" fetching={false} error={null}
    />
  );
}

test.each([false, true])("group pager shares the header and does not toggle expansion (actions=%s)", (actions) => {
  const onPageChange = vi.fn();
  const onToggle = vi.fn();
  render(
    <ResourceViewProvider scope="local">
      <Harness actions={actions} onPageChange={onPageChange} onToggle={onToggle} />
    </ResourceViewProvider>,
  );

  const nav = screen.getByRole("navigation", { name: "January records" });
  const header = screen.getByRole("button", { name: "January" }).closest("tr")!;
  expect(header.contains(nav)).toBe(true);
  expect(header.nextElementSibling?.textContent).toBe("Group body");
  expect(within(nav).getByText("21-40 / 45")).toBeTruthy();
  fireEvent.click(within(nav).getByRole("button", { name: "Next January records" }));
  expect(onPageChange).toHaveBeenCalledWith("january", 3);
  expect(onToggle).not.toHaveBeenCalled();
  fireEvent.click(within(nav).getByRole("button", { name: "Previous January records" }));
  expect(onPageChange).toHaveBeenLastCalledWith("january", 1);
  fireEvent.click(screen.getByRole("button", { name: "January" }));
  expect(onToggle).toHaveBeenCalledWith("january");
});

test.each(["last", "first", "only"] as const)("metric cells stay numeric with the measure %s", (position) => {
  const measure: ColumnDescriptor<Row> = { field: "amount", header: "Amount", aggregate: "sum" };
  const columns = position === "last" ? [...defaultColumns, measure]
    : position === "first" ? [measure, ...defaultColumns] : [measure];
  render(
    <ResourceViewProvider scope="local">
      <Harness columns={columns} onPageChange={vi.fn()} onToggle={vi.fn()} />
    </ResourceViewProvider>,
  );
  expect(screen.getByRole("cell", { name: "January Amount: 30" }).textContent).toBe("30");
  const label = screen.getByText("January");
  const nav = screen.getByRole("navigation", { name: "January records" });
  expect(label.closest("tr")).toBe(nav.closest("tr"));
  if (position === "only") {
    const toggle = screen.getByRole("button", { name: "January" });
    expect(toggle.parentElement).toBe(nav.parentElement);
    expect(toggle.parentElement?.className).toContain("flex");
  }
});

test("pending leaf reads keep the header pager mounted and disable its controls", () => {
  const props = { onPageChange: vi.fn(), onToggle: vi.fn() };
  const { rerender } = render(
    <ResourceViewProvider scope="local"><Harness {...props} /></ResourceViewProvider>,
  );
  const nav = screen.getByRole("navigation", { name: "January records" });
  rerender(
    <ResourceViewProvider scope="local"><Harness {...props} pending /></ResourceViewProvider>,
  );
  expect(screen.getByRole("navigation", { name: "January records" })).toBe(nav);
  expect(nav.getAttribute("aria-busy")).toBe("true");
  for (const button of within(nav).getAllByRole("button")) {
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
  }
  expect(props.onPageChange).not.toHaveBeenCalled();
});
