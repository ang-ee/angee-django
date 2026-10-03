// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"navigation":{"disableMainFrameNavigation":true,"disableChildPageNavigation":true}}}
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { afterEach, expect, test, vi } from "vitest";

import { InAppLinkProvider } from "../../lib/in-app-link";
import { BoardRowCard } from "./board/cards";
import { RecordRow } from "./list-body/rows";

const data = [{ id: "7", title: "Record seven" }];
const titleColumn = { accessorKey: "title", header: "Title" };
const columns = [titleColumn];
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function RecordLinks({ board = false, interactiveCell = false, open }: {
  board?: boolean;
  interactiveCell?: boolean;
  open: () => void;
}) {
  const table = useReactTable({ data, columns: interactiveCell ? [{ ...titleColumn, meta: { interactive: true }, cell: () => <button>Inspect</button> }] : columns, getCoreRowModel: getCoreRowModel() });
  const row = table.getRowModel().rows[0]!;
  if (board) return <BoardRowCard row={row} columns={[]} groupFields={new Set()} laneId="all" dragEnabled={false} sortable={false}
    cardActionContext={{ refresh: () => undefined }} renderCard={() => "Record seven"} rowHref={() => "/records/7?view=all"} onRecordOpen={open} />;
  return <table><tbody><RecordRow row={row} selected={false} onToggleSelected={() => undefined} interactive selectable={false}
    rowHref={() => "/records/7?view=all"} onRecordOpen={open} /></tbody></table>;
}

test.each([false, true])("record links use the provider, retaining query and plain-click preparation (board=%s)", (board) => {
  const navigate = vi.fn();
  const open = vi.fn();
  render(<InAppLinkProvider navigate={navigate}><RecordLinks board={board} open={open} /></InAppLinkProvider>);
  const link = screen.getByRole("link");
  expect(fireEvent.click(link, { ctrlKey: true })).toBe(true);
  expect(open).not.toHaveBeenCalled();
  expect(navigate).not.toHaveBeenCalled();
  expect(fireEvent.click(link)).toBe(false);
  expect(open).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7?view=all");
});

test("whole-row clicks delegate to the anchor, preserving the row's explicit new-tab behavior", () => {
  const navigate = vi.fn();
  const open = vi.fn();
  const newTab = vi.spyOn(window, "open").mockReturnValue(null);
  render(<InAppLinkProvider navigate={navigate}><RecordLinks open={open} /></InAppLinkProvider>);
  const row = screen.getByRole("row");
  fireEvent.click(row, { button: 2 });
  expect(navigate).not.toHaveBeenCalled();
  fireEvent.click(row, { metaKey: true });
  expect(newTab).toHaveBeenCalledExactlyOnceWith("/records/7?view=all", "_blank", "noopener");
  expect(navigate).not.toHaveBeenCalled();
  fireEvent.click(row);
  expect(open).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7?view=all");
});

test("a row with its own interactive cell follows only when the row itself is activated", () => {
  const navigate = vi.fn();
  const open = vi.fn();
  render(<InAppLinkProvider navigate={navigate}><RecordLinks interactiveCell open={open} /></InAppLinkProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
  expect(navigate).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole("row"), { key: "Enter" });
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7?view=all");
  expect(open).toHaveBeenCalledOnce();
});
