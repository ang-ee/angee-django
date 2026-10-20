import * as v from "valibot";

export const COMPONENT_KINDS = [
  "token", "primitive", "fragment", "region", "surface", "page", "contribution",
] as const;
export const REGION_PLACES = [
  "aside-head", "breadcrumb", "command-band", "command-palette", "control-band",
  "drawer-rail", "explorer-pane", "navigation-rail", "page-footer", "page-header",
  "page-toolbar", "pane-toolbar-band", "reader", "record-header", "section-navigation",
  "status-line", "top-bar", "user-menu",
] as const;
export type ComponentKind = (typeof COMPONENT_KINDS)[number];
export type RegionPlace = (typeof REGION_PLACES)[number];

const Kind = v.nullable(v.picklist(COMPONENT_KINDS));
export const CatalogEntrySchema = v.pipe(v.strictObject({
  package: v.picklist(["@angee/ui", "@angee/app"]),
  name: v.string(),
  source: v.string(),
  kind: Kind,
  owner: v.optional(v.string()),
  region: v.optional(v.picklist(REGION_PLACES)),
  reason: v.optional(v.string()),
  status: v.picklist(["stable", "deprecated"]),
  storyId: v.nullable(v.string()),
}), v.check(entry => (entry.kind === "region") === Boolean(entry.region), "A region must name a shell place."),
v.check(entry => entry.kind !== null || Boolean(entry.reason), "Infrastructure requires an explicit reason."));
export type CatalogEntry = v.InferOutput<typeof CatalogEntrySchema>;
export type CatalogStatus = CatalogEntry["status"];
export const CatalogSchema = v.array(CatalogEntrySchema);

/** The composition graph uses the same ontology as the export catalog. */
export const CompositionReportSchema = v.object({
  version: v.literal(0),
  source: v.string(),
  nodes: v.array(v.looseObject({
    id: v.string(), name: v.string(), prefix: v.string(),
    kind: Kind, kindBasis: v.string(), reason: v.optional(v.string()),
    shipped: v.boolean(), ownershipViolation: v.boolean(),
    routeInstance: v.optional(v.boolean(), false),
  })),
  edges: v.array(v.object({
    from: v.string(), to: v.string(), file: v.nullable(v.string()), source: v.string(),
  })),
  fan_in: v.record(v.string(), v.pipe(v.number(), v.integer(), v.minValue(0))),
  hand_rolled: v.array(v.string()),
  duplicates: v.record(v.string(), v.array(v.string())),
  pages: v.array(v.string()),
});
export type CompositionReport = v.InferOutput<typeof CompositionReportSchema>;
export const parseCompositionReport = (input: unknown): CompositionReport => v.parse(CompositionReportSchema, input);
export const parseCatalog = (input: unknown): CatalogEntry[] => v.parse(CatalogSchema, input);
