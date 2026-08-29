import * as React from "react";
import { type Row as TableRowModel } from "@tanstack/react-table";
import type { AggregateBucket, GroupDimension as HasuraGroupDimension, GroupOrder } from "@angee/refine";
import type { ModelMetadata, Row } from "@angee/metadata";
import { groupDimensionForField, groupDimensionForGroup, groupExtractionForGroup, relationFilterForRelation } from "@angee/metadata";
import { format, startOfISOWeek } from "date-fns";
import { Glyph } from "../../../chrome/Glyph";
import { useUiT, type UiTranslate } from "../../../i18n";
import { statusLabel } from "../../../lib/labels";
import { TableCell, TableRow } from "../../../ui/table";
import { dateFromUnknown } from "../../../widgets/date-format";
import type { ResourceViewGroup } from "../resource-view-model";
import { enumValueLabel } from "./cell-utils";
import type { GroupByDimension } from "./types";
export function GroupHeader<TRow extends Row>({
  row,
  colSpan,
  groupStack,
}: {
  row: TableRowModel<TRow>;
  colSpan: number;
  groupStack: readonly ResourceViewGroup[];
}): React.ReactElement {
  const t = useUiT();
  const canExpand = row.getCanExpand();
  const expanded = row.getIsExpanded();
  const label = groupedRowLabel(row, groupStack, t("list.emptyValue"), t);
  const rowCount = row.getLeafRows().length;
  const indent = { paddingLeft: `calc(0.75rem + ${row.depth * 1.25}rem)` };
  // The chevron only appears when the header is a toggle; the lead/trailing
  // content is identical either way, so it is rendered once and the branch
  // chooses only the wrapper (interactive button vs static row).
  const content = (
    <>
      <span className="inline-flex min-w-0 items-center gap-2 font-semibold text-fg">
        {canExpand ? (
          <Glyph
            name={expanded ? "chevron-down" : "chevron-right"}
            className="size-3.5 shrink-0 text-fg-muted"
          />
        ) : null}
        <span className="min-w-0 truncate">{label}</span>
        <span className="font-normal text-fg-muted">
          {rowCount.toLocaleString()}
        </span>
      </span>
    </>
  );
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="h-8 bg-sheet-2 p-0">
        {canExpand ? (
          // aria-controls is omitted deliberately: the group's rows are loose
          // virtualized siblings with no stable container id to reference.
          <button
            type="button"
            className="flex h-8 w-full min-w-0 items-center justify-between gap-3 px-3 text-left text-13 outline-none hover:bg-inset focus-visible:focus-ring"
            style={indent}
            aria-expanded={expanded}
            onClick={() => row.toggleExpanded()}
          >
            {content}
          </button>
        ) : (
          <div
            className="flex h-8 items-center justify-between gap-3 px-3 text-13"
            style={indent}
          >
            {content}
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}

/**
 * The display label of a TanStack grouped row — the one owner. TanStack groups
 * on the locale-stable `groupKey`; only this render boundary translates it.
 */
export function groupedRowLabel<TRow extends Row>(
  row: TableRowModel<TRow>,
  groupStack: readonly ResourceViewGroup[],
  emptyValueLabel: string,
  t: UiTranslate,
): string {
  const columnId = row.groupingColumnId;
  const value = columnId ? row.getGroupingValue(columnId) : undefined;
  const group = groupStack.find((candidate) => candidate.field === columnId)
    ?? groupStack[row.depth];
  if (!group) return value == null || value === "" ? emptyValueLabel : String(value);
  return groupLabelFromKey(value, group, emptyValueLabel, t);
}

export function resourceViewGroupToAggregateDimension(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): GroupByDimension {
  const dimension = groupDimensionForGroup(group, metadata);
  const extraction = groupExtractionForGroup(dimension, group);
  return {
    field: dimension.input,
    key: extraction?.key ?? dimension.key,
    ...(extraction ? { granularity: extraction.input } : {}),
    ...(extraction?.rangeKey ? { rangeKey: extraction.rangeKey } : {}),
  };
}

export function hasuraGroupDimension(
  dimension: GroupByDimension,
): HasuraGroupDimension {
  return {
    input: dimension.field,
    key: dimension.key ?? dimension.field,
    ...(dimension.granularity ? { granularity: dimension.granularity } : {}),
    ...(dimension.rangeKey ? { rangeKey: dimension.rangeKey } : {}),
  };
}

export function hasuraGroupOrderForDimensions(
  dimensions: readonly HasuraGroupDimension[],
): readonly GroupOrder[] | undefined {
  const dimension = dimensions.length > 1
    ? dimensions[dimensions.length - 1]
    : dimensions[0];
  const field = dimension?.key ?? dimension?.input;
  return field ? [{ field, direction: "ASC", nulls: "LAST" }] : undefined;
}

/**
 * The extra group-by dimension that carries a relation group's display label —
 * the same bucket grouped by `<relation>__<label>` so the related record's name
 * rides along with its id (Odoo's `(id, display_name)`). `null` when the model
 * registers no label axis for the relation, in which case the group labels by id.
 */
export function groupLabelDimension(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): GroupByDimension | null {
  const labelKey = groupLabelKey(group, metadata);
  if (!labelKey) return null;
  const dimension = groupDimensionForField(labelKey, metadata);
  return { field: dimension.input, key: dimension.key };
}

function groupLabelKey(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): string | undefined {
  const field = group.aggregateField;
  if (!field) return undefined;
  return relationFilterForRelation(field, metadata)?.labelKey;
}

export function bucketValueLabels(
  bucket: AggregateBucket,
  groupStack: readonly ResourceViewGroup[],
  metadata: ModelMetadata | null,
  emptyValueLabel: string,
  t: UiTranslate,
  emptyRelationLabel?: (field: string) => string,
): string[] {
  return groupStack.map((group) => {
    const labelKey = groupLabelKey(group, metadata);
    if (labelKey) {
      const label = bucket.key?.[groupDimensionForField(labelKey, metadata).key];
      if (label != null && label !== "") return String(label);
      return emptyRelationLabel?.(group.aggregateField ?? group.field)
        ?? emptyValueLabel;
    }
    const dimension = resourceViewGroupToAggregateDimension(group, metadata);
    const value = bucket.key?.[dimension.key ?? dimension.field];
    return groupLabel(value, group, metadata, emptyValueLabel, t);
  });
}

const EMPTY_GROUP_KEY = "__angee_empty_group__";

export function groupKey(
  value: unknown,
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): string {
  if (value == null) return EMPTY_GROUP_KEY;
  const enumLabel = typeof value === "string"
    ? enumLabelFromMetadata(metadata, group.field, value)
    : null;
  if (enumLabel) return enumLabel;
  const date = dateFromUnknown(value);
  if (!date) {
    if (typeof value !== "string") return String(value);
    // Only an enum-typed field's raw member name gets prettified; free-text
    // values (mailbox names, relation labels) must render verbatim — title-casing
    // mangles them ("CATC" -> "Catc", "B.V." -> "B V"). Gate on the field KIND,
    // not the values list — an enum whose values projected empty still prettifies.
    const isEnumField = metadata?.fields[group.field]?.kind === "enum";
    return isEnumField ? statusLabel(value) : value;
  }
  if (group.granularity === "year") return String(date.getFullYear());
  if (group.granularity === "quarter") {
    const quarter = Math.floor(date.getMonth() / 3) + 1;
    return `${date.getFullYear()}-Q${quarter}`;
  }
  if (group.granularity === "month") {
    return format(date, "yyyy-MM");
  }
  if (group.granularity === "week") {
    return format(startOfISOWeek(date), "yyyy-MM-dd");
  }
  return format(date, "yyyy-MM-dd");
}

export function groupLabel(
  value: unknown,
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
  emptyValueLabel: string,
  t: UiTranslate,
): string {
  return groupLabelFromKey(
    groupKey(value, group, metadata),
    group,
    emptyValueLabel,
    t,
  );
}

function groupLabelFromKey(
  value: unknown,
  group: ResourceViewGroup,
  emptyValueLabel: string,
  t: UiTranslate,
): string {
  if (value == null || value === "" || value === EMPTY_GROUP_KEY) return emptyValueLabel;
  const key = String(value);
  if (group.granularity === "quarter") {
    const match = /^(\d{4})-Q([1-4])$/.exec(key);
    if (match) {
      return t("list.quarter", {
        year: Number(match[1]),
        quarter: Number(match[2]),
      });
    }
  }
  if (group.granularity === "month") {
    const date = dateFromGroupKey(key);
    if (date) return format(date, "MMMM yyyy");
  }
  if (group.granularity === "week") {
    const date = dateFromGroupKey(key);
    if (date) return t("list.weekOf", { date: format(date, "MMMM d, yyyy") });
  }
  if (!group.granularity) {
    const date = dateFromGroupKey(key);
    if (date) return format(date, "MMMM d, yyyy");
  }
  return key;
}

function dateFromGroupKey(key: string): Date | null {
  const match = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(key);
  if (!match) return null;
  const date = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3] ?? "1"),
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

function enumLabelFromMetadata(
  metadata: ModelMetadata | null,
  field: string,
  value: string,
): string | null {
  const fieldMetadata = metadata?.fields[field];
  const values = fieldMetadata?.values ?? [];
  const normalized = normalizeEnumValue(value);
  const option = values.find(
    (candidate) =>
      candidate.value === value
      || normalizeEnumValue(candidate.value) === normalized,
  );
  return option ? enumValueLabel(option) : null;
}

function normalizeEnumValue(value: string): string {
  return value.trim().replace(/[\s-]+/g, "_").toLowerCase();
}
