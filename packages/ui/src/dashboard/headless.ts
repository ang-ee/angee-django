import type { ComponentType, ReactNode } from "react";
import * as v from "valibot";

export const DASHBOARD_SCHEMA_VERSION = 1 as const;
export const DASHBOARD_COLUMNS = 12;
export const DASHBOARD_STORE_SLOT = "dashboard.store";
export const DASHBOARD_WIDGET_KINDS_SLOT = "dashboard.widget-kinds";

export const DASHBOARD_LIMITS = {
  columns: { min: 1, max: 24 },
  widgets: 100,
  snapshotBytes: 256 * 1024,
  widgetHeight: 100,
  layoutRows: 1_000,
  rowResults: 100,
  seriesBuckets: 500,
  displayTopN: { min: 1, max: 20 },
} as const;

const JsonValueSchema: v.GenericSchema<unknown, unknown> = v.lazy(() =>
  v.union([
    v.null(),
    v.string(),
    v.number(),
    v.boolean(),
    v.array(JsonValueSchema),
    v.record(v.string(), JsonValueSchema),
  ]),
);

const PositiveInteger = v.pipe(v.number(), v.integer(), v.minValue(1));
const NonNegativeInteger = v.pipe(v.number(), v.integer(), v.minValue(0));

export const DashboardTargetSchema = v.variant("scope", [
  v.strictObject({ scope: v.literal("addon"), key: v.pipe(v.string(), v.minLength(1)) }),
  v.strictObject({ scope: v.literal("resource"), key: v.pipe(v.string(), v.minLength(1)) }),
  v.strictObject({ scope: v.literal("personal"), id: v.pipe(v.string(), v.minLength(1)) }),
]);

export const DashboardRefreshSchema = v.variant("mode", [
  v.strictObject({ mode: v.literal("live") }),
  v.strictObject({
    mode: v.literal("interval"),
    seconds: v.pipe(PositiveInteger, v.maxValue(3_600), v.minValue(5)),
  }),
  v.strictObject({ mode: v.literal("manual") }),
]);

export const WidgetMeasureSchema = v.strictObject({
  op: v.picklist(["count", "sum", "avg", "min", "max"]),
  field: v.optional(v.pipe(v.string(), v.minLength(1))),
});

export const WidgetSourceSchema = v.strictObject({
  resource: v.pipe(v.string(), v.minLength(1)),
  filter: v.optional(v.record(v.string(), JsonValueSchema)),
  groups: v.optional(v.array(v.strictObject({
    field: v.pipe(v.string(), v.minLength(1)),
    granularity: v.optional(v.pipe(v.string(), v.minLength(1))),
  }))),
  sort: v.optional(v.array(v.strictObject({
    field: v.pipe(v.string(), v.minLength(1)),
    direction: v.picklist(["ASC", "DESC"]),
  }))),
  measure: v.optional(WidgetMeasureSchema),
  fields: v.optional(v.array(v.pipe(v.string(), v.minLength(1)))),
  limit: v.optional(v.pipe(PositiveInteger, v.maxValue(DASHBOARD_LIMITS.rowResults))),
  refresh: v.optional(DashboardRefreshSchema),
});

export const WidgetDataSpecSchema = v.variant("shape", [
  v.strictObject({ shape: v.literal("value"), source: WidgetSourceSchema }),
  v.strictObject({ shape: v.literal("series"), source: WidgetSourceSchema }),
  v.strictObject({ shape: v.literal("rows"), source: WidgetSourceSchema }),
  v.strictObject({
    shape: v.literal("none"),
    binding: v.strictObject({
      dashboardKey: v.pipe(v.string(), v.minLength(1)),
      widgetId: v.pipe(v.string(), v.minLength(1)),
    }),
  }),
]);

export const WidgetSpecSchema = v.strictObject({
  schemaVersion: v.literal(DASHBOARD_SCHEMA_VERSION),
  id: v.pipe(v.string(), v.minLength(1)),
  definitionRef: v.optional(v.pipe(v.string(), v.minLength(1))),
  kind: v.pipe(v.string(), v.minLength(1)),
  kindVersion: PositiveInteger,
  title: v.string(),
  data: WidgetDataSpecSchema,
  options: v.record(v.string(), JsonValueSchema),
  x: NonNegativeInteger,
  y: NonNegativeInteger,
  w: PositiveInteger,
  h: v.pipe(PositiveInteger, v.maxValue(DASHBOARD_LIMITS.widgetHeight)),
  isArchived: v.boolean(),
});

export const DashboardSnapshotSchema = v.strictObject({
  schemaVersion: v.literal(DASHBOARD_SCHEMA_VERSION),
  columns: v.pipe(
    PositiveInteger,
    v.minValue(DASHBOARD_LIMITS.columns.min),
    v.maxValue(DASHBOARD_LIMITS.columns.max),
  ),
  widgets: v.pipe(v.array(WidgetSpecSchema), v.maxLength(DASHBOARD_LIMITS.widgets)),
});

export type DashboardTarget = v.InferOutput<typeof DashboardTargetSchema>;
export type DashboardRefresh = v.InferOutput<typeof DashboardRefreshSchema>;
export type WidgetMeasure = v.InferOutput<typeof WidgetMeasureSchema>;
export type WidgetSource = v.InferOutput<typeof WidgetSourceSchema>;
export type WidgetDataSpec = v.InferOutput<typeof WidgetDataSpecSchema>;
export type WidgetSpec = v.InferOutput<typeof WidgetSpecSchema>;
export type DashboardSnapshot = v.InferOutput<typeof DashboardSnapshotSchema>;
export type WidgetDataShape = Exclude<WidgetDataSpec["shape"], "none"> | "none";

export interface DashboardDefinition {
  key: string;
  title: string;
  revision: string;
  columns?: number;
  resource?: string;
  widgets: readonly WidgetSpec[];
  routeName?: string;
  /** Code-only components for shape=none widgets, keyed by stable widget id. */
  authored?: Readonly<Record<string, ComponentType>>;
}

export interface DashboardCapabilities {
  canEdit: boolean;
  canReset: boolean;
  canArchive: boolean;
}

export type DashboardLoadState =
  | { status: "loading" }
  | { status: "absent" }
  | { status: "forbidden"; message?: string }
  | { status: "unavailable"; message?: string }
  | { status: "error"; error: Error }
  | {
      status: "ready";
      snapshot: DashboardSnapshot;
      persistedId: string;
      revision: number;
      name: string;
      description?: string;
      capabilities: DashboardCapabilities;
    };

export interface DashboardSaveCommand {
  target: DashboardTarget;
  persistedId: string | null;
  expectedRevision: number | null;
  declarationRevision?: string;
  name?: string;
  description?: string;
  snapshot: DashboardSnapshot;
}

export interface DashboardSaveResult {
  persistedId: string;
  revision: number;
  snapshot: DashboardSnapshot;
  capabilities: DashboardCapabilities;
}

export interface DashboardSummary {
  id: string;
  target: DashboardTarget;
  title: string;
  description?: string;
  owner?: string;
  resources: readonly string[];
  revision: number;
  customized: boolean;
  available: boolean;
  capabilities: DashboardCapabilities;
}

export interface DashboardStoreBinding {
  state: DashboardLoadState;
  reload: () => Promise<void>;
  save: (command: DashboardSaveCommand) => Promise<DashboardSaveResult>;
  reset: (target: DashboardTarget, persistedId: string, expectedRevision: number) => Promise<void>;
  createPersonal: (input: { name: string; description?: string; clientCreationKey: string }) => Promise<DashboardSaveResult>;
  duplicate: (target: DashboardTarget, input: { name: string; clientCreationKey: string }) => Promise<DashboardSaveResult>;
  archive: (id: string, expectedRevision: number, archived: boolean) => Promise<DashboardSaveResult>;
}

export interface DashboardCatalogueBinding {
  summaries: readonly DashboardSummary[];
  loading: boolean;
  error: Error | null;
  refresh: () => void;
  createPersonal: (input: { name: string; description?: string; clientCreationKey: string }) => Promise<DashboardSaveResult>;
}

export interface DashboardStore {
  useDashboard: (target: DashboardTarget) => DashboardStoreBinding;
  useCatalogue: () => DashboardCatalogueBinding;
}

export interface DashboardWidgetData {
  value: number | null;
  series: readonly { key: string; label: string; value: number }[];
  rows: readonly Record<string, unknown>[];
  fetching: boolean;
  error: Error | null;
  live: boolean;
  updatedAt: number | null;
  refetch: () => void;
}

export interface DashboardWidgetRenderProps {
  spec: WidgetSpec;
  data: DashboardWidgetData;
  authored?: ReactNode;
}

export interface DashboardWidgetKind {
  id: string;
  contributionId: string;
  version: number;
  label: string;
  shape: WidgetDataShape;
  defaultSize: { w: number; h: number };
  minSize: { w: number; h: number };
  Component: ComponentType<DashboardWidgetRenderProps>;
  replaces?: string;
}

export interface DashboardRegistry {
  definitions: Readonly<Record<string, DashboardDefinition>>;
  resourceDefaults: Readonly<Record<string, string>>;
  widgetKinds: Readonly<Record<string, DashboardWidgetKind>>;
  store: DashboardStore | null;
}

export function dashboardTargetKey(target: DashboardTarget): string {
  return target.scope === "personal"
    ? `personal:${target.id}`
    : `${target.scope}:${target.key}`;
}

export function parseDashboardSnapshot(value: unknown): DashboardSnapshot {
  const result = v.safeParse(DashboardSnapshotSchema, value);
  if (!result.success) {
    const issue = result.issues[0];
    const path = issue.path?.map(({ key }) => `[${String(key)}]`).join("") ?? "";
    throw new Error(`Invalid dashboard snapshot${path}: ${issue.message}`);
  }
  const encoded = JSON.stringify(result.output);
  if (new TextEncoder().encode(encoded).byteLength > DASHBOARD_LIMITS.snapshotBytes) {
    throw new Error(`Dashboard snapshot exceeds ${DASHBOARD_LIMITS.snapshotBytes} bytes.`);
  }
  validateDashboardLayout(result.output);
  return result.output;
}

export function validateDashboardLayout(snapshot: DashboardSnapshot): void {
  const ids = new Set<string>();
  const active = snapshot.widgets.filter((widget) => !widget.isArchived);
  for (const widget of snapshot.widgets) {
    if (ids.has(widget.id)) throw new Error(`Duplicate dashboard widget id "${widget.id}".`);
    ids.add(widget.id);
    if (widget.x + widget.w > snapshot.columns) {
      throw new Error(`Widget "${widget.id}" extends beyond the dashboard columns.`);
    }
    if (widget.y + widget.h > DASHBOARD_LIMITS.layoutRows) {
      throw new Error(`Widget "${widget.id}" extends beyond the dashboard row limit.`);
    }
  }
  for (let leftIndex = 0; leftIndex < active.length; leftIndex += 1) {
    const left = active[leftIndex]!;
    for (let rightIndex = leftIndex + 1; rightIndex < active.length; rightIndex += 1) {
      const right = active[rightIndex]!;
      const overlaps = left.x < right.x + right.w
        && right.x < left.x + left.w
        && left.y < right.y + right.h
        && right.y < left.y + left.h;
      if (overlaps) throw new Error(`Dashboard widgets "${left.id}" and "${right.id}" overlap.`);
    }
  }
}
