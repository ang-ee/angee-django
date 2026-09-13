// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { BoardView } from "./BoardView";
import type { RowGroup } from "./resource-view-list-body";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ColumnDescriptor } from "../page";
import type { Row } from "@angee/metadata";

const dndMocks = vi.hoisted(() => {
  const PointerSensor = function PointerSensor() {};
  const KeyboardSensor = function KeyboardSensor() {};
  return {
    PointerSensor,
    KeyboardSensor,
    contextProps: null as {
      sensors?: unknown;
      collisionDetection?: (args: unknown) => unknown;
      onDragEnd?: (event: unknown) => void;
    } | null,
    pointerWithin: vi.fn((): unknown[] => []),
    rectIntersection: vi.fn((): unknown[] => []),
    setActivatorNodeRef: vi.fn(),
    onDragPointerDown: vi.fn(),
    onDragKeyDown: vi.fn(),
    useSensor: vi.fn((sensor: unknown, options?: unknown) => ({ sensor, options })),
    useSensors: vi.fn((...sensors: unknown[]) => sensors),
    useDraggable: vi.fn(() => ({
      attributes: { "data-draggable": "true" },
      listeners: {
        onPointerDown: (event: unknown) => dndMocks.onDragPointerDown(event),
        onKeyDown: (event: unknown) => dndMocks.onDragKeyDown(event),
      },
      setNodeRef: vi.fn(),
      setActivatorNodeRef: vi.fn((node) => dndMocks.setActivatorNodeRef(node)),
      transform: null,
      isDragging: false,
    })),
    useSortable: vi.fn(() => ({
      attributes: { "data-sortable": "true" },
      listeners: {
        onPointerDown: (event: unknown) => dndMocks.onDragPointerDown(event),
        onKeyDown: (event: unknown) => dndMocks.onDragKeyDown(event),
      },
      setNodeRef: vi.fn(),
      setActivatorNodeRef: vi.fn((node) => dndMocks.setActivatorNodeRef(node)),
      transform: null,
      transition: undefined,
      isDragging: false,
    })),
    useDroppable: vi.fn(() => ({
      setNodeRef: vi.fn(),
      isOver: false,
    })),
  };
});

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../../i18n", () => ({ useUiT: () => (key: string) => key }));
vi.mock("@dnd-kit/core", () => ({
  DndContext: (props: {
    children: React.ReactNode;
    sensors?: unknown;
    collisionDetection?: (args: unknown) => unknown;
    onDragEnd?: (event: unknown) => void;
  }) => {
    dndMocks.contextProps = props;
    return props.children;
  },
  PointerSensor: dndMocks.PointerSensor,
  KeyboardSensor: dndMocks.KeyboardSensor,
  closestCenter: "closestCenter",
  pointerWithin: dndMocks.pointerWithin,
  rectIntersection: dndMocks.rectIntersection,
  useSensor: dndMocks.useSensor,
  useSensors: dndMocks.useSensors,
  useDraggable: dndMocks.useDraggable,
  useDroppable: dndMocks.useDroppable,
}));
vi.mock("@dnd-kit/sortable", () => ({
  SortableContext: ({ children }: { children: React.ReactNode }) => children,
  sortableKeyboardCoordinates: "sortableKeyboardCoordinates",
  useSortable: dndMocks.useSortable,
  verticalListSortingStrategy: "verticalListSortingStrategy",
}));

beforeEach(() => {
  dndMocks.contextProps = null;
  dndMocks.useSensor.mockClear();
  dndMocks.useSensors.mockClear();
  dndMocks.useDraggable.mockClear();
  dndMocks.useSortable.mockClear();
  dndMocks.useDroppable.mockClear();
  dndMocks.pointerWithin.mockClear();
  dndMocks.rectIntersection.mockClear();
  dndMocks.pointerWithin.mockReturnValue([]);
  dndMocks.rectIntersection.mockReturnValue([]);
  dndMocks.setActivatorNodeRef.mockClear();
  dndMocks.onDragPointerDown.mockClear();
  dndMocks.onDragKeyDown.mockClear();
});
afterEach(() => cleanup());

interface DemoRow extends Row {
  id: string;
  label: string;
  sort_order?: number;
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

  test("makes the whole column a drop target, not just its cards", () => {
    renderBoard({
      groups: [
        lane([{ id: "1", label: "First" }]),
        { ...lane([]), key: "empty", label: "Empty" },
      ],
      dragEnabled: true,
      onCardMove: vi.fn(),
    });

    // The lane's droppable node is its frame, so a frame that stops at its last
    // card leaves the space below it belonging to nobody -- which is why a drop
    // into an empty column, or below a short one's cards, did nothing.
    const laneRegion = screen.getByRole("region", { name: "Empty" });
    const surface = laneRegion.parentElement;
    expect(surface?.className).toContain("items-stretch");
    expect(surface?.className).not.toContain("items-start");

    // Every lane is a droppable, the empty one included.
    const droppableIds = dndMocks.useDroppable.mock.calls.map(
      (call) => (call as unknown as readonly [{ id: string }])[0].id,
    );
    expect(droppableIds).toContain("board-lane:empty");
    expect(laneRegion.className).not.toContain("self-start");
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

  test("wires dnd-kit pointer and keyboard sensors when card moves are enabled", () => {
    const onCardMove = vi.fn();
    renderBoard({
      dragEnabled: true,
      onCardMove,
    });

    expect(dndMocks.contextProps).not.toBeNull();
    expect(dndMocks.useSensor).toHaveBeenCalledWith(
      dndMocks.PointerSensor,
      { activationConstraint: { distance: 6 } },
    );
    expect(dndMocks.useSensor).toHaveBeenCalledWith(
      dndMocks.KeyboardSensor,
      { coordinateGetter: "sortableKeyboardCoordinates" },
    );

    act(() => {
      dndMocks.contextProps?.onDragEnd?.({
        active: {
          data: {
            current: {
              row: { id: "1", label: "Notes" },
              laneId: "data",
            },
          },
        },
        over: {
          id: "board-lane:next",
          data: { current: { type: "board-lane", laneId: "next" } },
        },
      });
    });

    expect(onCardMove).toHaveBeenCalledWith({ id: "1", label: "Notes" }, "next");
  });

  test("uses pointer collision with rectangle fallback for card moves", () => {
    const pointerHit = [{ id: "pointer" }];
    const rectHit = [{ id: "rect" }];
    dndMocks.pointerWithin.mockReturnValueOnce(pointerHit);
    dndMocks.rectIntersection.mockReturnValueOnce(rectHit);

    renderBoard({
      dragEnabled: true,
      onCardMove: vi.fn(),
    });

    expect(dndMocks.contextProps?.collisionDetection?.({})).toBe(pointerHit);
    dndMocks.pointerWithin.mockReturnValueOnce([]);
    expect(dndMocks.contextProps?.collisionDetection?.({})).toBe(rectHit);
  });

  test("makes the whole card the drag activator, not the grip alone", () => {
    renderBoard({
      dragEnabled: true,
      onCardMove: vi.fn(),
      rowHref: () => "/records/1",
    });

    // dnd-kit hears the gesture through its listeners, and those must be on the
    // card: with them on the grip alone the card body is a plain link, which the
    // browser drags natively, so the card never moves and the drag looks dead.
    const card = document.querySelector("article");
    expect(card).toBeTruthy();
    fireEvent.pointerDown(card as Element);
    expect(dndMocks.onDragPointerDown).toHaveBeenCalledTimes(1);

    // The a11y attributes stay on the grip. On the card they would make every
    // card a focusable role=button wrapping a link and a button, and the card
    // and the grip would then claim the same aria-describedby.
    expect(card?.getAttribute("data-draggable")).toBe(null);
    expect(document.querySelectorAll("[data-draggable='true']").length).toBe(1);
    expect(
      screen.getByRole("button", { name: "board.dragCard" }).getAttribute("data-draggable"),
    ).toBe("true");

    // The grip has no listeners of its own; its keydown reaches dnd-kit by
    // bubbling to the card, which is what keeps keyboard drag working.
    fireEvent.keyDown(screen.getByRole("button", { name: "board.dragCard" }), { code: "Space" });
    expect(dndMocks.onDragKeyDown).toHaveBeenCalledTimes(1);

    // A touch drag must not scroll the lane instead of moving the card.
    expect(card?.className).toContain("touch-none");

    // And the body link must not start a native drag that steals the gesture.
    expect(card?.querySelector("a")?.getAttribute("draggable")).toBe("false");
  });

  test("makes a sortable card the drag activator too", () => {
    renderBoard({
      groups: [lane([{ id: "1", label: "First", sort_order: 1024 }])],
      dragEnabled: true,
      rankField: "sort_order",
      onCardMove: vi.fn(),
      rowHref: () => "/records/1",
    });

    const card = document.querySelector("article");
    expect(card).toBeTruthy();
    fireEvent.pointerDown(card as Element);
    expect(dndMocks.onDragPointerDown).toHaveBeenCalledTimes(1);

    expect(card?.getAttribute("data-sortable")).toBe(null);
    expect(document.querySelectorAll("[data-sortable='true']").length).toBe(1);
    expect(card?.querySelector("a")?.getAttribute("draggable")).toBe("false");
  });

  test("wires a card drag handle as the keyboard activator", () => {
    renderBoard({
      dragEnabled: true,
      onCardMove: vi.fn(),
    });

    expect(screen.getByRole("button", { name: "board.dragCard" })).toBeTruthy();
    expect(dndMocks.setActivatorNodeRef).toHaveBeenCalled();
  });

  test("requests the midpoint rank when a sortable card moves inside its lane", () => {
    const onCardMove = vi.fn();
    renderBoard({
      groups: [
        lane([
          { id: "1", label: "First", sort_order: 1024 },
          { id: "2", label: "Second", sort_order: 2048 },
          { id: "3", label: "Third", sort_order: 3072 },
        ]),
      ],
      dragEnabled: true,
      rankField: "sort_order",
      onCardMove,
    });

    act(() => {
      dndMocks.contextProps?.onDragEnd?.({
        active: {
          id: "1",
          data: {
            current: {
              type: "board-card",
              row: { id: "1", label: "First", sort_order: 1024 },
              laneId: "data",
            },
          },
        },
        over: {
          id: "2",
          data: { current: { type: "board-card", laneId: "data" } },
        },
      });
    });

    expect(dndMocks.useSortable).toHaveBeenCalledTimes(3);
    expect(onCardMove).toHaveBeenCalledWith(
      { id: "1", label: "First", sort_order: 1024 },
      "data",
      2560,
    );
  });

  test("ignores drag-end events with malformed card data", () => {
    const onCardMove = vi.fn();
    renderBoard({
      dragEnabled: true,
      onCardMove,
    });

    act(() => {
      dndMocks.contextProps?.onDragEnd?.({
        active: {
          data: {
            current: {
              row: { id: "1", label: "Notes" },
              laneId: 2,
            },
          },
        },
        over: {
          id: "board-lane:next",
          data: { current: { type: "board-lane", laneId: "next" } },
        },
      });
    });

    expect(onCardMove).not.toHaveBeenCalled();
  });

  test("passes null when a card is dropped on the empty lane", () => {
    const onCardMove = vi.fn();
    renderBoard({
      dragEnabled: true,
      onCardMove,
    });

    act(() => {
      dndMocks.contextProps?.onDragEnd?.({
        active: {
          data: {
            current: {
              row: { id: "1", label: "Notes" },
              laneId: "data",
            },
          },
        },
        over: {
          id: "board-lane:",
          data: { current: { type: "board-lane", laneId: "" } },
        },
      });
    });

    expect(onCardMove).toHaveBeenCalledWith({ id: "1", label: "Notes" }, null);
  });
});
