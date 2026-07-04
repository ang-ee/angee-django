import type { DataResourceLinesMetadata, Row } from "@angee/metadata";
import type { LineInput } from "@angee/refine";

import { relationValueId } from "../widgets/types";

/**
 * The facts the line diff needs from the child-lines contract: which field owns a
 * row's public id, which integer column carries the drag-maintained order, the
 * editable child columns serialized into each `LineInput`, and which of those are
 * relations (whose value normalizes to the related record's id).
 */
export interface LineDiffConfig {
  idField: string;
  positionField: string | null;
  fieldNames: readonly string[];
  relationFields: ReadonlySet<string>;
}

/** Derive the diff config from a resource's editable-lines metadata. */
export function lineDiffConfig(lines: DataResourceLinesMetadata): LineDiffConfig {
  const fields = lines.fields ?? [];
  return {
    idField: "id",
    positionField: lines.positionField ?? null,
    fieldNames: fields.map((field) => field.name),
    relationFields: new Set(
      fields.filter((field) => field.kind === "relation").map((field) => field.name),
    ),
  };
}

/**
 * The result of diffing the edited line rows against their loaded baseline. The
 * backend `<resource>_save(pk, patch, lines)` mutation owns the actual apply: it
 * receives `payload` — the *full* desired child list, each existing row carrying its
 * `id` (update) and each new row omitting it (create) — and deletes any stored row
 * whose id is absent from that list. `created`/`updated`/`deleted` classify the same
 * change set for dirty detection and tests; `deleted` is the implicit backend delete.
 */
export interface LineDiff {
  payload: LineInput[];
  created: LineInput[];
  updated: LineInput[];
  deleted: string[];
  hasChanges: boolean;
}

/** Serialize one edited row into a `LineInput`, taking `position` from row order. */
export function lineToInput(
  row: Row,
  index: number,
  config: LineDiffConfig,
): LineInput {
  const input: LineInput = {};
  const id = rowId(row, config);
  if (id) input.id = id;
  for (const name of config.fieldNames) {
    if (config.positionField && name === config.positionField) continue;
    input[name] = lineFieldValue(row, name, config);
  }
  if (config.positionField) input[config.positionField] = index;
  return input;
}

/**
 * Diff the current field-array rows against the loaded baseline rows. Both sides are
 * normalized line rows (scalars and relation ids), so the comparison is like-for-like;
 * relation values are compared by id so an untouched nested read matches a picked id.
 */
export function diffLines(
  baseline: readonly Row[],
  current: readonly Row[],
  config: LineDiffConfig,
): LineDiff {
  const baselineById = new Map<string, Row>();
  for (const row of baseline) {
    const id = rowId(row, config);
    if (id) baselineById.set(id, row);
  }
  const payload: LineInput[] = [];
  const created: LineInput[] = [];
  const updated: LineInput[] = [];
  const currentIds = new Set<string>();
  current.forEach((row, index) => {
    const input = lineToInput(row, index, config);
    payload.push(input);
    if (input.id === undefined) {
      created.push(input);
      return;
    }
    currentIds.add(input.id);
    const base = baselineById.get(input.id);
    if (!base || !sameLine(base, row, index, config)) updated.push(input);
  });
  const deleted = [...baselineById.keys()].filter((id) => !currentIds.has(id));
  return {
    payload,
    created,
    updated,
    deleted,
    hasChanges: created.length > 0 || updated.length > 0 || deleted.length > 0,
  };
}

/**
 * Normalize a record's loaded lines into field-array rows: keep the public id, the
 * editable child columns, and a `position` (from the stored value, else row order).
 * Used to seed the composer and as the diff baseline, so an unedited save is a no-op.
 */
export function recordLinesToRows(
  lines: unknown,
  config: LineDiffConfig,
): Row[] {
  if (!Array.isArray(lines)) return [];
  return lines.map((line, index) => rowFromLine(asRow(line), index, config));
}

/** A blank row for the composer's "add line" action, its `position` at the tail. */
export function emptyLineRow(index: number, config: LineDiffConfig): Row {
  const row: Row = {};
  for (const name of config.fieldNames) {
    row[name] = config.relationFields.has(name) ? null : "";
  }
  if (config.positionField) row[config.positionField] = index;
  return row;
}

/** Duplicate a row for the composer's "duplicate" action, dropping its identity. */
export function duplicateLineRow(row: Row, config: LineDiffConfig): Row {
  const { [config.idField]: _id, ...rest } = row;
  return { ...rest };
}

function rowFromLine(line: Row, index: number, config: LineDiffConfig): Row {
  const row: Row = {};
  const id = rowId(line, config);
  if (id) row[config.idField] = id;
  for (const name of config.fieldNames) row[name] = line[name];
  if (config.positionField) {
    const stored = line[config.positionField];
    row[config.positionField] = typeof stored === "number" ? stored : index;
  }
  return row;
}

function sameLine(
  base: Row,
  current: Row,
  index: number,
  config: LineDiffConfig,
): boolean {
  for (const name of config.fieldNames) {
    if (config.positionField && name === config.positionField) continue;
    if (
      compareKey(lineFieldValue(base, name, config)) !==
      compareKey(lineFieldValue(current, name, config))
    ) {
      return false;
    }
  }
  if (config.positionField) {
    const basePosition = base[config.positionField];
    if (typeof basePosition !== "number" || basePosition !== index) return false;
  }
  return true;
}

function lineFieldValue(row: Row, name: string, config: LineDiffConfig): unknown {
  return config.relationFields.has(name) ? relationValueId(row[name]) : row[name];
}

function rowId(row: Row, config: LineDiffConfig): string | undefined {
  const value = row[config.idField];
  if (typeof value === "string" && value !== "") return value;
  if (typeof value === "number") return String(value);
  return undefined;
}

function compareKey(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function asRow(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Row)
    : {};
}
