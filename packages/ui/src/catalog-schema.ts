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
export const CatalogSchema = v.array(CatalogEntrySchema);

export const parseCatalog = (input: unknown): CatalogEntry[] => v.parse(CatalogSchema, input);
