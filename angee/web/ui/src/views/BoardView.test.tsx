// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { BoardView } from "./BoardView";
import type { RowGroup } from "./ListInternals";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ColumnDescriptor } from "./page";
import type { Row } from "@angee/resources";

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../i18n", () => ({ useBaseT: () => (key: string) => key }));

interface DemoRow extends Row {
  id: string;
  label: string;
}

// BoardView consumes precomputed rows + a minimal view context; build just the shape
// it reads (the row's id/original and an empty group stack) rather than the whole
// TanStack table/resource-view machinery.
function lane(rows: readonly DemoRow[]): RowGroup<DemoRow> {
  return {
    key: "data",
    label: "Data",
    path: [],
    depth: 0,
    rows: rows.map((row) => ({ id: row.id, original: row }) as never),
    children: [],
  };
}

const RESOURCE_VIEW = {
  state: { groupStack: [] },
} as unknown as ResourceViewContextValue;

const COLUMNS = [{ field: "label", header: "Label" }] as ColumnDescriptor<DemoRow>[];

function renderBoard(props: Partial<Parameters<typeof BoardView<DemoRow>>[0]> = {}) {
  return render(
    <BoardView<DemoRow>
      columns={COLUMNS}
      groups={[lane([{ id: "1", label: "Notes" }])]}
      resourceView={RESOURCE_VIEW}
      selectedIds={new Set()}
      interactive={false}
      emptyMessage="empty"
      {...props}
    />,
  );
}

describe("BoardView", () => {
  test("renders the default key/value body from columns", () => {
    renderBoard();
    expect(screen.getByText("Notes")).toBeTruthy();
  });

  test("renderCard overrides the card body while the actions footer still renders", () => {
    renderBoard({
      renderCard: (row) => <div data-testid="custom">{row.label.toUpperCase()}</div>,
      cardActions: (row) => <button type="button">act {row.label}</button>,
    });
    expect(screen.getByTestId("custom").textContent).toBe("NOTES");
    expect(screen.getByRole("button", { name: "act Notes" })).toBeTruthy();
  });
});
