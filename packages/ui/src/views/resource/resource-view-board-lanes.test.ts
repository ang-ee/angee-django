import { describe, expect, test } from "vitest";
import type { Row } from "@angee/metadata";
import type { Row as TableRowModel } from "@tanstack/react-table";

import {
  rowGroupsFromLaneSource,
  type ResolvedBoardLaneSource,
} from "./resource-view-board-lanes";

interface LeadRow extends Row {
  id: string;
  stage: { id: string; name: string } | null;
}

function tableRow(original: LeadRow): TableRowModel<LeadRow> {
  return {
    id: original.id,
    original,
    subRows: [],
    getIsGrouped: () => false,
  } as unknown as TableRowModel<LeadRow>;
}

function laneSource(nullable: boolean): ResolvedBoardLaneSource {
  return {
    field: "stage",
    labelField: "name",
    relation: {
      modelLabel: "crm.Stage",
      resource: "crm.Stage",
      labelField: "name",
      relationObject: true,
    },
    fieldMetadata: {
      name: "stage",
      kind: "relation",
      nullable,
    },
  } as unknown as ResolvedBoardLaneSource;
}

describe("rowGroupsFromLaneSource", () => {
  test("keeps declared empty lanes and appends undeclared server values", () => {
    const groups = rowGroupsFromLaneSource(
      [
        tableRow({ id: "lead_1", stage: { id: "new", name: "New" } }),
        tableRow({ id: "lead_2", stage: { id: "legacy", name: "Legacy" } }),
      ],
      laneSource(true),
      [
        { value: "new", label: "New" },
        { value: "won", label: "Won" },
      ],
      new Map(),
      "No stage",
      "Unknown stage",
    );

    expect(groups.map(({ key, label, rows }) => ({ key, label, rows: rows.length })))
      .toEqual([
        { key: "new", label: "New", rows: 1 },
        { key: "won", label: "Won", rows: 0 },
        { key: "legacy", label: "Legacy", rows: 1 },
      ]);
  });

  test.each([
    [true, undefined],
    [false, true],
  ] as const)(
    "labels an empty lane and sets drop-disabled according to nullable=%s",
    (nullable, dropDisabled) => {
      const [group] = rowGroupsFromLaneSource(
        [tableRow({ id: "lead_1", stage: null })],
        laneSource(nullable),
        [],
        new Map(),
        "No stage",
        "Unknown stage",
      );

      expect(group).toMatchObject({ key: "", label: "No stage", rows: [{ id: "lead_1" }] });
      expect(group?.dropDisabled).toBe(dropDisabled);
    },
  );
});
