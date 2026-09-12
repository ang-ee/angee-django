import type { DataResourceMetadata, SchemaFieldMetadata } from "@angee/metadata";
import { titleCase } from "../lib/titleCase";
import type {
  DashboardRegistry,
  DashboardWidgetKind,
  WidgetDataSpec,
} from "./headless";

export interface DashboardWidgetCatalogueEntry {
  id: string;
  section: string;
  resource: string;
  title: string;
  kind: string;
  kindLabel: string;
  data: WidgetDataSpec;
  size: { w: number; h: number };
}

const INCLUDED_KINDS = ["stat", "bar", "donut", "table"] as const;
const NON_SCALAR_FIELDS = new Set(["json", "list", "object"]);
const SKIPPED_TABLE_FIELDS = new Set([
  "id",
  "created_by",
  "updated_by",
  "created_by_label",
  "updated_by_label",
  "metadata",
]);
const APP_LABELS: Readonly<Record<string, string>> = {
  iam: "IAM",
  mcp: "MCP",
  oidc: "OIDC",
  uom: "Units of measure",
  vcs: "VCS",
};

/** Ready-to-use widget questions derived from the resources the host composed. */
export function buildDashboardWidgetCatalogue(
  metadata: SchemaFieldMetadata,
  registry: DashboardRegistry,
  preferredResource?: string,
): DashboardWidgetCatalogueEntry[] {
  const kinds = new Map(
    INCLUDED_KINDS.flatMap((id) => {
      const kind = registry.widgetKinds[id];
      return kind ? [[id, kind] as const] : [];
    }),
  );
  const seen = new Set<string>();
  const entries = metadata.resources.flatMap((resource) => {
    if (seen.has(resource.modelLabel)) return [];
    seen.add(resource.modelLabel);
    return entriesForResource(resource, kinds);
  });
  return entries.sort((left, right) => {
    const leftPreferred = left.resource === preferredResource ? 0 : 1;
    const rightPreferred = right.resource === preferredResource ? 0 : 1;
    return leftPreferred - rightPreferred
      || left.section.localeCompare(right.section)
      || left.title.localeCompare(right.title)
      || left.kindLabel.localeCompare(right.kindLabel);
  });
}

function entriesForResource(
  resource: DataResourceMetadata,
  kinds: ReadonlyMap<string, DashboardWidgetKind>,
): DashboardWidgetCatalogueEntry[] {
  const entries: DashboardWidgetCatalogueEntry[] = [];
  const noun = titleCase(resource.modelLabel.split(".").at(-1) || resource.modelName || resource.modelLabel);
  const common = {
    section: APP_LABELS[resource.appLabel.toLowerCase()] ?? titleCase(resource.appLabel),
    resource: resource.modelLabel,
  };
  const stat = kinds.get("stat");
  if (stat?.shape === "value" && resource.roots.aggregate) {
    entries.push({
      ...common,
      id: `${resource.modelLabel}:total`,
      title: `${noun} total`,
      kind: stat.id,
      kindLabel: stat.label,
      data: {
        shape: "value",
        source: { resource: resource.modelLabel, measure: { op: "count" } },
      },
      size: stat.defaultSize,
    });
  }

  if (resource.roots.groups) {
    const axes = Object.values(resource.query.axes).filter((axis) => {
      const field = resource.query.fields[axis.field];
      return Boolean(axis.server && field && !NON_SCALAR_FIELDS.has(field.kind));
    });
    for (const axis of axes) {
      const granularity = axis.kind === "date"
        ? axis.extractions.find(({ name }) => name === "month")?.name
        : undefined;
      const group = { field: axis.field, ...(granularity ? { granularity } : {}) };
      const axisLabel = titleCase(axis.field);
      for (const id of ["bar", "donut"] as const) {
        const kind = kinds.get(id);
        if (kind?.shape !== "series") continue;
        entries.push({
          ...common,
          id: `${resource.modelLabel}:by:${axis.field}:${id}`,
          title: `${noun} by ${axisLabel.toLowerCase()}`,
          kind: kind.id,
          kindLabel: kind.label,
          data: {
            shape: "series",
            source: {
              resource: resource.modelLabel,
              groups: [group],
              measure: { op: "count" },
              limit: 8,
            },
          },
          size: kind.defaultSize,
        });
      }
    }
  }

  const table = kinds.get("table");
  const fields = tableFields(resource);
  if (table?.shape === "rows" && resource.roots.list && fields.length > 0) {
    const updatedAt = resource.query.fields.updated_at?.sort ? "updated_at" : null;
    entries.push({
      ...common,
      id: `${resource.modelLabel}:rows`,
      title: updatedAt ? `Recent ${noun.toLowerCase()} records` : `${noun} records`,
      kind: table.id,
      kindLabel: table.label,
      data: {
        shape: "rows",
        source: {
          resource: resource.modelLabel,
          fields,
          ...(updatedAt ? { sort: [{ field: updatedAt, direction: "DESC" as const }] } : {}),
          limit: 10,
        },
      },
      size: table.defaultSize,
    });
  }
  return entries;
}

function tableFields(resource: DataResourceMetadata): string[] {
  const identity = resource.query.identity.field;
  const preferred = [resource.recordRepresentation, identity]
    .filter((field): field is string => Boolean(field));
  const readable = Object.entries(resource.query.fields)
    .filter(([name, field]) => Boolean(
      field.row
      && !SKIPPED_TABLE_FIELDS.has(name)
      && !NON_SCALAR_FIELDS.has(field.kind),
    ))
    .map(([name]) => name);
  return [...new Set([...preferred, ...readable])]
    .filter((field) => Boolean(resource.query.fields[field]?.row))
    .slice(0, 5);
}
