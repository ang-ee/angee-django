import * as React from "react";
import { type Row as TableRowModel } from "@tanstack/react-table";
import type { AggregateBucket, AggregateMeasure } from "@angee/refine";
import type { ModelMetadata, Row } from "@angee/metadata";
import { bucketFilterForGroup as metadataBucketFilterForGroup } from "@angee/metadata";
import type { ResourceListOrder, ResourceViewFilter, ResourceViewGroup } from "../resource-view-model";
import type { ColumnDescriptor, PageColumnAlign } from "../../page";
export function bucketFilterForGroup(
  bucket: AggregateBucket,
  group: ResourceViewGroup | undefined,
  metadata: ModelMetadata | null,
): ResourceViewFilter | undefined {
  return metadataBucketFilterForGroup(bucket, group, metadata) as
    | ResourceViewFilter
    | undefined;
}

export type ColumnAlign = PageColumnAlign;
export type ListColumn<TRow extends Row = Row> = ColumnDescriptor<TRow>;

export interface VisibleFieldOption {
  id: string;
  label: React.ReactNode;
  visible: boolean;
  disabled?: boolean;
}

export interface GroupByDimension {
  field: string;
  key?: string;
  granularity?: string;
  rangeKey?: string;
}

export type RowGroup<TRow extends Row> = {
  key: string;
  label: string | null;
  path: readonly string[];
  depth: number;
  rows: readonly TableRowModel<TRow>[];
  children: readonly RowGroup<TRow>[];
  declared?: boolean;
  dropDisabled?: boolean;
  /** Lane-resource hint used only as the board fold's initial value. */
  defaultCollapsed?: boolean;
};

export interface GroupMeasure extends AggregateMeasure {
  field: string;
  columnId: string;
  label: string;
  unit: string;
}

/**
 * One windowed row of a server-grouped list. The grouped surface flattens its
 * group tree (per-level `_groups` headers, the leaf record rows of expanded
 * buckets, and the per-group pagers) into this stream and feeds it to the shared
 * `useVirtualizer`; the thin grouped body renders each kind. Every variant is
 * self-describing so the renderer never re-decodes a bucket or filter — the
 * surface that owns the data put the rendered facts here.
 */
/**
 * The sibling-list a record opens into from a server group: the leaf bucket's
 * cumulative filter/order/page that drives the detail view's prev/next. Carried
 * on each record row so the thin body can report it on open without re-deriving
 * the bucket (kept structural — no `resource-view-surface` import — to stay a leaf
 * module the surface can depend on).
 */
export interface GroupedRecordNav {
  filter: ResourceViewFilter | undefined;
  order: ResourceListOrder | undefined;
  page: number;
  pageSize: number;
  rows: readonly Row[];
  total: number | undefined;
  fetching: boolean;
}

export type GroupedListItem<TRow extends Row> =
  | {
      kind: "groupHeader";
      /** The cumulative path key the surface toggles/expands on. */
      bucketKey: string;
      depth: number;
      label: string;
      count: number;
      expandable: boolean;
      expanded: boolean;
      bucket: AggregateBucket;
    }
  | { kind: "record"; itemKey: string; row: TableRowModel<TRow>; nav: GroupedRecordNav }
  | {
      kind: "pager";
      /** The page-state key the surface pages on (a level or leaf scope). */
      pageKey: string;
      depth: number;
      label: string;
      page: number;
      pageSize: number;
      total: number;
      unit: "groups" | "records";
    }
  | { kind: "skeleton"; itemKey: string; depth: number; rowCount: number }
  | {
      kind: "status";
      itemKey: string;
      depth: number;
      message: React.ReactNode;
      tone: "muted" | "danger";
    };

/** Estimated row height per grouped item kind, in lockstep with the rendered CSS. */
export function estimateGroupedItemSize<TRow extends Row>(
  item: GroupedListItem<TRow> | undefined,
): number {
  switch (item?.kind) {
    case "groupHeader":
      return GROUP_HEADER_HEIGHT;
    case "record":
      return RECORD_ROW_HEIGHT;
    case "pager":
      return PAGER_ROW_HEIGHT;
    case "skeleton":
      return Math.max(1, item.rowCount) * SKELETON_ROW_HEIGHT;
    case "status":
      return GROUP_STATUS_HEIGHT;
    default:
      return RECORD_ROW_HEIGHT;
  }
}

export const ALIGN_CLASS: Record<PageColumnAlign, string> = {
  left: "text-left",
  center: "text-center",
  right: "text-right",
};
export const LIST_VIEW_SCROLL_BUDGET = "calc(100vh - 12rem)";
export const TABLE_SCROLL_STYLE: React.CSSProperties = {
  maxHeight: LIST_VIEW_SCROLL_BUDGET,
};
export const GROUP_ROW_HEIGHT = 32;
export const RECORD_ROW_HEIGHT = 40;
/** Server-grouped header row (`h-9`); taller than the flat `h-8` group header. */
export const GROUP_HEADER_HEIGHT = 36;
/** In-body pager row (the sub-group and leaf-record pagers). */
export const PAGER_ROW_HEIGHT = 44;
/** A single skeleton/placeholder row while a grouped page loads. */
export const SKELETON_ROW_HEIGHT = 40;
/** A single empty/error status row inside the grouped body. */
export const GROUP_STATUS_HEIGHT = 52;
