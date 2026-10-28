import { describe, expect, test } from "vitest";
import type { Row } from "@angee/metadata";

import type { RowGroup } from "./resource-view-list-body";
import {
  boardMoveForDrop,
  fractionalRankBetween,
} from "./board-ordering";

type RankedRow = Row & { id: string; sort_order: number };

describe("board ordering requests", () => {
  test("uses the FractionalRankField prepend, midpoint, and append convention", () => {
    expect(fractionalRankBetween(undefined, undefined)).toBe(1024);
    expect(fractionalRankBetween(undefined, 1024)).toBe(0);
    expect(fractionalRankBetween(1024, 2048)).toBe(1536);
    expect(fractionalRankBetween(2048, undefined)).toBe(3072);
  });

  test("computes first, middle, and last positions inside one lane", () => {
    const groups = [
      group("lane", [
        { id: "a", sort_order: 1024 },
        { id: "b", sort_order: 2048 },
        { id: "c", sort_order: 3072 },
      ]),
    ];
    const rankForRow = (row: RowGroup<RankedRow>["rows"][number]) =>
      row.original.sort_order;

    expect(boardMoveForDrop({
      activeRowId: "c",
      activeLaneId: "lane",
      target: { laneId: "lane", rowId: "a" },
      groups,
      rankField: "sort_order",
      rankForRow,
    })).toEqual({ laneId: "lane", rank: 0 });
    expect(boardMoveForDrop({
      activeRowId: "a",
      activeLaneId: "lane",
      target: { laneId: "lane", rowId: "b" },
      groups,
      rankField: "sort_order",
      rankForRow,
    })).toEqual({ laneId: "lane", rank: 2560 });
    expect(boardMoveForDrop({
      activeRowId: "a",
      activeLaneId: "lane",
      target: { laneId: "lane" },
      groups,
      rankField: "sort_order",
      rankForRow,
    })).toEqual({ laneId: "lane", rank: 4096 });
  });

  test("computes the destination lane's neighbours for a cross-lane move", () => {
    const groups = [
      group("source", [{ id: "a", sort_order: 1024 }]),
      group("target", [
        { id: "b", sort_order: 1024 },
        { id: "c", sort_order: 2048 },
      ]),
    ];

    expect(boardMoveForDrop({
      activeRowId: "a",
      activeLaneId: "source",
      target: { laneId: "target", rowId: "c" },
      groups,
      rankField: "sort_order",
      rankForRow: (row) => row.original.sort_order,
    })).toEqual({ laneId: "target", rank: 1536 });
  });

  test("sends loaded public neighbors in visual order without evaluating a numeric rank", () => {
    const groups = [
      group("source", [{ id: "active", sort_order: 1024 }]),
      group("target", [
        { id: "before", sort_order: 1024 },
        { id: "following", sort_order: 2048 },
        { id: "last", sort_order: 3072 },
      ]),
    ];
    expect(boardMoveForDrop({
      activeRowId: "active",
      activeLaneId: "source",
      target: { laneId: "target", rowId: "following" },
      groups,
      rankField: "sort_order",
      rankInputField: "task_order",
      rankForRow: () => { throw new Error("Intent must not allocate a rank"); },
    })).toEqual({
      laneId: "target",
      order: { preceding: ["before"], following: ["following", "last"] },
    });
  });

  test("excludes the active row and expresses an empty-lane insertion explicitly", () => {
    const request = {
      activeRowId: "active", activeLaneId: "source",
      groups: [group("source", [
        { id: "active", sort_order: 1024 },
        { id: "other", sort_order: 2048 },
      ]), group("empty", [])],
      rankField: "sort_order", rankInputField: "task_order",
      rankForRow: () => { throw new Error("Intent must not allocate a rank"); },
    };
    expect(boardMoveForDrop({ ...request, target: { laneId: "source" } })).toEqual({
      laneId: "source", order: { preceding: ["other"], following: [] },
    });
    expect(boardMoveForDrop({ ...request, target: { laneId: "empty" } })).toEqual({
      laneId: "empty", order: { preceding: [], following: [] },
    });
  });

  test("preserves the legacy cross-lane-only contract without a rank fact", () => {
    const groups = [group("source", [{ id: "a", sort_order: 1024 }])];
    const rankForRow = (row: RowGroup<RankedRow>["rows"][number]) =>
      row.original.sort_order;

    expect(boardMoveForDrop({
      activeRowId: "a",
      activeLaneId: "source",
      target: { laneId: "source" },
      groups,
      rankForRow,
    })).toBeNull();
    expect(boardMoveForDrop({
      activeRowId: "a",
      activeLaneId: "source",
      target: { laneId: "target" },
      groups,
      rankForRow,
    })).toEqual({ laneId: "target" });
  });
});

function group(
  key: string,
  rows: readonly RankedRow[],
): RowGroup<RankedRow> {
  return {
    key,
    label: key,
    path: [key],
    depth: 0,
    rows: rows.map((row) => ({ id: row.id, original: row }) as never),
    children: [],
    declared: true,
  };
}
