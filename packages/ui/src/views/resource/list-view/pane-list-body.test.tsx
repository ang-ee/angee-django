// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { PaneRow } from "../../../communication/pane-list";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { useRowsResourceViewSurface } from "../resource-view-surface";
import { PaneListBody } from "./pane-list-body";

afterEach(cleanup);
const rows = [{ id: "a", title: "Ada", category: "People" }, { id: "b", title: "Grace", category: "People" }];
const columns = [{ field: "title", header: "Name" }, { field: "category", header: "Category" }];

function Pane({ openGroup = () => {} }: { openGroup?: (value: unknown) => void }) {
  const resourceView = useResourceView();
  const surface = useRowsResourceViewSurface({ rows, columns, resourceView });
  return <PaneListBody surface={surface} groupStack={resourceView.state.groupStack}
    renderGroupLabel={({ bucket, label }) => <button onClick={() => openGroup(bucket)}>Open {label}</button>}
    renderRow={(row) => <PaneRow title={row.title} />} emptyContent="No people" />;
}

test("pane labels follow native client group expansion without rendering a group as a record", () => {
  const open = vi.fn();
  render(<ResourceViewProvider scope="local" initialState={{ groupStack: [{ field: "category" }] }}><Pane openGroup={open} /></ResourceViewProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Open People" }));
  expect(open).toHaveBeenCalledWith({ key: { category: "People" }, count: 2 });
  const group = screen.getByRole("button", { name: "Expand People" });
  expect(screen.getAllByText("Open People")).toHaveLength(1);
  expect(group.contains(screen.getByRole("button", { name: "Open People" }))).toBe(false);
  expect(screen.queryByText("Ada")).toBeNull();
  fireEvent.click(group);
  expect(screen.getAllByText("Ada")).toHaveLength(1);
  expect(screen.getAllByText("Grace")).toHaveLength(1);
  fireEvent.click(group);
  expect(screen.queryByText("Ada")).toBeNull();
  fireEvent.click(group);
  expect(screen.getAllByText("Ada")).toHaveLength(1);
});
