import { ResourceQuery, type DataResourceMetadata } from "@angee/metadata";
import { GraphQLError, type GraphQLWireOperation } from "@angee/refine/testing/wire";
import { tz } from "@date-fns/tz";
import { add, parseISO, startOfQuarter, startOfYear, startOfMonth, startOfWeek, startOfDay, startOfHour, startOfMinute, startOfSecond } from "date-fns";

type Row = { id: string; [key: string]: unknown };
type Args = Record<string, unknown>;

/** Read-only resource snapshots for full-page workshops. Metadata owns wire names,
 * filtering, relation representation and group projections; this supplies sample rows.
 * Authored reports/actions still need explicit GraphQL wire handlers.
 */
export function resourceSnapshotOperations(resources: readonly DataResourceMetadata[], samples: Readonly<Record<string, readonly Row[]>>,
  state: "populated" | "empty" | "error" = "populated"): Record<string, GraphQLWireOperation> {
  const byModel = new Map(resources.map((resource) => [resource.modelLabel, resource]));
  function wireRow(model: string, row: Row): Row {
    const resource = byModel.get(model);
    const fields = resource?.fields ?? [];
    return { ...Object.fromEntries(fields.map((field) => [field.name, field.kind === "list" ? [] : null])), ...row,
      ...Object.fromEntries(fields.filter((field) => field.relationModelLabel && row[field.name] != null).map((field) => {
        const relation = (value: unknown): unknown => {
          if (!value || typeof value !== "object") return value;
          return field.relationObject || resource?.linesResource?.field === field.name
            ? wireRow(field.relationModelLabel!, value as Row) : (value as Row).id;
        };
        return [field.name, Array.isArray(row[field.name]) ? (row[field.name] as unknown[]).map(relation) : relation(row[field.name])];
      })),
    };
  }
  const operations: Record<string, GraphQLWireOperation> = {};
  for (const resource of resources) {
    const rows = (samples[resource.modelLabel] ?? []).map((row) => wireRow(resource.modelLabel, row));
    const query = ResourceQuery.from(resource);
    const selected = (args: Args) => {
      if (state === "error") throw new GraphQLError("Sample connection error. Choose Populated in Storybook Controls to recover.");
      if (state === "empty") return [];
      const filter = query.fromWhere(args.where ?? {});
      return rows.filter((row) => query.matches(row, filter));
    };
    const aggregate = (items: readonly Row[]) => ({ count: items.length,
      sum: Object.fromEntries((resource.aggregateMeasures ?? []).filter((measure) => measure.op === "sum" && measure.field)
        .map((measure) => [measure.field!, items.reduce((total, row) => total + Number(query.value(measure.field!, row) ?? 0), 0)])),
    });
    const groups = (args: Args) => {
      const dimensions = (args.group_by ?? []) as { field: string; granularity?: string }[];
      const buckets = new Map<string, { key: Record<string, unknown>; rows: Row[] }>();
      for (const row of selected(args)) {
        const key: Record<string, unknown> = {};
        for (const dimension of dimensions) {
          const declaration = Object.values(query.axes).find((axis) => axis.server?.input === dimension.field || axis.server?.labelInput === dimension.field);
          if (!declaration?.server) throw new Error(`Snapshot does not recognize ${resource.modelLabel} group ${dimension.field}.`);
          const extraction = declaration.extractions.find((item) => item.input === dimension.granularity);
          const axis = query.axis(declaration.field, extraction?.name);
          const projection = axis.groupBy();
          const identity = axis.identity(row);
          if (extraction?.rangeKey && identity != null) {
            const date = parseISO(String(query.value(declaration.field, row)), { in: tz("UTC") });
            const intervals = {
              year: [startOfYear(date), { years: 1 }], quarter: [startOfQuarter(date), { months: 3 }],
              month: [startOfMonth(date), { months: 1 }], week: [startOfWeek(date, { weekStartsOn: 1 }), { weeks: 1 }],
              day: [startOfDay(date), { days: 1 }], hour: [startOfHour(date), { hours: 1 }],
              minute: [startOfMinute(date), { minutes: 1 }], second: [startOfSecond(date), { seconds: 1 }],
            } as const;
            const interval = intervals[extraction.name as keyof typeof intervals];
            if (!interval) throw new Error(`Snapshot range needs an explicit fixture for ${extraction.name}.`);
            key[extraction.rangeKey] = { from: interval[0].toISOString(), to: add(interval[0], interval[1]).toISOString() };
          }
          const mapping = declaration.server.valueMap?.find((entry) => Object.is(entry.to, identity));
          key[projection.valueKey] = mapping ? mapping.from : identity;
          if (projection.labelKey) {
            const relation = resource.fields?.find((field) => field.name === declaration.field)?.relationModelLabel;
            key[projection.labelKey] = declaration.labelPath ? axis.label(row)
              : samples[relation ?? ""]?.find((item) => item.id === identity)?.display_name ?? identity;
          }
        }
        const id = JSON.stringify(key);
        if (!buckets.has(id)) buckets.set(id, { key, rows: [] });
        buckets.get(id)!.rows.push(row);
      }
      const having = args.having as { count_gt?: number } | undefined;
      return [...buckets.values()]
        .filter((bucket) => having?.count_gt === undefined || bucket.rows.length > having.count_gt)
        .sort(byGroupOrder(args.order_by))
        .map((bucket) => ({ ...bucket, aggregate: aggregate(bucket.rows) }));
    };
    const { roots } = resource;
    if (roots.list) operations[roots.list] = (args) => selected(args).slice(Number(args.offset ?? 0), args.limit == null ? undefined : Number(args.offset ?? 0) + Number(args.limit));
    if (roots.detail) operations[roots.detail] = ({ id }) => selected({}).find((row) => row.id === id) ?? null;
    if (roots.aggregate) operations[roots.aggregate] = (args) => ({ aggregate: aggregate(selected(args)) });
    if (roots.groups) operations[roots.groups] = groups;
    if (roots.groupsCount) operations[roots.groupsCount] = (args) => groups(args).length;
    for (const root of [roots.create, roots.update, roots.save, roots.delete]) {
      if (root) operations[root] = () => { throw new GraphQLError("This is a read-only UX snapshot. Changes are not saved; reload to reset."); };
    }
  }
  return operations;
}

/**
 * A groups read's `order_by` as a server applies it, term after term: each key of the group in its direction, a
 * null key where the term's `nulls` says, else where SQL puts it (last ascending, first descending). A read that
 * orders by a label and then an identity breaks label ties by identity. Without an order, first appearance.
 */
function byGroupOrder(orderBy: unknown): (left: { key: Record<string, unknown> }, right: { key: Record<string, unknown> }) => number {
  const terms = (Array.isArray(orderBy) ? orderBy : []) as readonly { field: string; direction?: string; nulls?: string | null }[];
  return (left, right) => {
    for (const { field, direction, nulls } of terms) {
      const descending = direction === "DESC";
      const [a, b] = [left.key[field] ?? null, right.key[field] ?? null];
      if (a === null || b === null) {
        if (a === b) continue;
        return (a === null) === ((nulls ?? (descending ? "FIRST" : "LAST")) === "LAST") ? 1 : -1;
      }
      const order = typeof a === "number" && typeof b === "number" ? a - b : String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0;
      if (order !== 0) return descending ? -order : order;
    }
    return 0;
  };
}
