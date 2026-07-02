// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { BoardView } from "./BoardView";
import type { RowGroup } from "./resource-view-list-body";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ColumnDescriptor } from "./page";
import type { Row } from "@angee/metadata";

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../i18n", () => ({ useUiT: () => (key: string) => key }));

afterEach(() => cleanup());

interface DemoRow extends Row {
  id: string;
  label: string;
  tags?: readonly string[];
  wordCount?: number;
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
      emptyContent="empty"
      {...props}
    />,
  );
}

describe("BoardView", () => {
  test("renders the default key/value body from columns", () => {
    renderBoard();
    expect(screen.getByText("Notes")).toBeTruthy();
  });

  test("lets the browser own board overflow instead of internal board scrollbars", () => {
    renderBoard();

    const laneRegion = screen.getByRole("region", { name: "Data" });
    const surface = laneRegion.parentElement;
    const card = screen.getByText("Notes").closest("article");
    const laneBody = card?.parentElement;

    expect(surface?.className).not.toContain("overflow-x-auto");
    expect(surface?.className).not.toContain("overflow-y-hidden");
    expect(surface?.className).not.toContain("h-full");
    expect(surface?.style.height).toBe("");
    expect(laneBody?.className).not.toContain("overflow-y-auto");
  });

  test("keeps default card detail rows inside the fixed lane width", () => {
    renderBoard({
      columns: [
        { field: "label", header: "Label" },
        { field: "tags", header: "Tags" },
        { field: "wordCount", header: "Word Count" },
      ],
      groups: [
        lane([
          {
            id: "1",
            label: "Release train status (translated: ES / FR / DE)",
            tags: ["engineering", "release", "translation"],
            wordCount: 155,
          },
        ]),
      ],
    });

    const card = screen.getByText(/Release train status/).closest("article");
    // A card with no href/onClick renders its frame as a plain <div> (the
    // anchor/button variants are reserved for genuinely interactive cards).
    const frame = card?.firstElementChild;
    const wordCountRow = screen.getByText("Word Count").closest("div");
    const wordCountValue = screen.getByText("155").closest("span");

    expect(card?.className).toContain("min-w-0");
    expect(card?.className).toContain("board-card-grid");
    expect(frame?.className).toContain("min-w-0");
    expect(frame?.className).toContain("max-w-full");
    expect(wordCountRow?.className).toContain("grid");
    expect(wordCountRow?.className).toContain("board-card-detail-grid");
    expect(wordCountValue?.className).toContain("overflow-hidden");
    expect(wordCountValue?.className).toContain("[overflow-wrap:anywhere]");
  });

  test("lets the browser own loading-board overflow too", () => {
    renderBoard({
      fetching: true,
      groups: [lane([])],
    });

    const surface = screen.getByRole("status");

    expect(surface.className).not.toContain("overflow-x-auto");
    expect(surface.className).not.toContain("overflow-y-hidden");
    expect(surface.className).not.toContain("h-full");
    expect(surface.style.height).toBe("");
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
