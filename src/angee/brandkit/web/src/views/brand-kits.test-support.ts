import {
  GraphQLError, testDataResource, testQueryAxis, testQueryField, testResourceQuery,
  type GraphQLWireOperation,
} from "@angee/storybook/testing";

import { BRAND_KIT_MODEL } from "./BrandKitForm";

type ResourceFacts = NonNullable<Parameters<typeof testDataResource>[1]>;
type FieldFacts = NonNullable<ResourceFacts["fields"]>[number];

export const COLOR_FIELDS = ["brand", "accent", "neutral", "canvas", "surface", "rail", "success", "warning", "danger", "info"] as const;
const TEXT_FIELDS = ["footer_organization", "footer_address", "voice"] as const;
const WRITABLE = { creatable: true, updatable: true } as const;

/** One field as the console's metadata states it (`BrandKitType` and `_BRAND_KIT_RESOURCE` in `brandkit/schema.py`). */
function kitField(name: string, scalar: string | null, facts: Partial<FieldFacts> = {}): FieldFacts {
  return { name, kind: "scalar", scalar, widget: null, readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false, ...facts };
}

/** A non-null text column the list neither filters nor sorts by. */
function plainColumn(name: string) {
  return testQueryField(name, { nullable: false, filter: null });
}

/** A logo: a storage file the kit reads and no console root writes. */
function storageFile(name: string) {
  return testQueryField(name, {
    kind: "relation", scalar: "ID", filter: null,
    relation: { model: "storage.File", identityPath: `${name}.id`, labelPath: `${name}.title` },
    row: { path: `${name}.id`, paths: [`${name}.id`] },
  });
}

/**
 * The composed console resource for `brandkit.BrandKit`. The node's `redacted_fields`, the decisions
 * addon's `has_open_decisions` and integrate's record edges are other owners' contributions and stay out.
 */
export const brandKitResource = testDataResource(BRAND_KIT_MODEL, {
  canonicalLabel: BRAND_KIT_MODEL,
  resourceType: "brandkit/brand_kit",
  capabilities: ["list", "detail", "aggregate", "groups", "create", "update", "delete", "deletePreview"],
  recordRepresentation: "name",
  rowModel: "server",
  history: true,
  subtitle: { created: "created_at", updated: "updated_at", wordCount: null },
  roots: {
    list: "brand_kits", detail: "brand_kits_by_pk", aggregate: "brand_kits_aggregate", groups: "brand_kits_groups",
    groupsCount: "brand_kits_groups_count", create: "insert_brand_kits_one", update: "update_brand_kits_by_pk",
    delete: "delete_brand_kits_by_pk", deletePreview: "brand_kits_delete", save: null, changes: null,
  },
  typeNames: {
    node: "BrandKitType", query: "brandKits_Query", filter: "brand_kits_bool_exp", order: "brand_kits_order_by",
    aggregate: "brand_kits_aggregate", grouped: "brand_kits_group", groupKey: "brandKitsgroupkey", groupBySpec: "brandKitsgroupbyspec",
    groupOrder: "brandKitsgrouporder", having: "brandKitshaving", createInput: "brand_kits_insert_input",
    updateInput: "brand_kits_set_input", deletePayload: "DeletePreview",
  },
  fields: [
    kitField("id", "ID", { aggregatable: true }),
    kitField("display_name", "String"),
    kitField("name", "String", { ...WRITABLE, requiredOnCreate: true }),
    kitField("is_default", "Boolean", { ...WRITABLE, widget: "switch" }),
    ...COLOR_FIELDS.map((name) => kitField(name, "String", { ...WRITABLE, requiredOnCreate: true, widget: "color" })),
    kitField("font_heading", "String", { ...WRITABLE, requiredOnCreate: true }),
    kitField("font_body", "String", { ...WRITABLE, requiredOnCreate: true }),
    ...["logo", "logo_dark"].map((name) => kitField(name, null, {
      kind: "relation", relationModelLabel: "storage.File", relationObject: true, widget: "many2one",
    })),
    ...TEXT_FIELDS.map((name) => kitField(name, "String", WRITABLE)),
    kitField("created_at", "DateTime", { widget: "datetime" }),
    kitField("updated_at", "DateTime", { widget: "datetime" }),
    kitField("contrast_warnings", null, { kind: "list", widget: "angee.brandkit.contrastWarnings" }),
  ],
  createFields: ["name", "is_default", ...COLOR_FIELDS, "font_heading", "font_body", ...TEXT_FIELDS],
  updateFields: ["name", "is_default", ...COLOR_FIELDS, "font_heading", "font_body", ...TEXT_FIELDS],
  requiredCreateFields: ["name", ...COLOR_FIELDS, "font_heading", "font_body"],
  createArguments: [{ name: "idempotency_key", type: "String" }],
  updateArguments: [],
  saveArguments: [],
  aggregateFields: ["id"],
  aggregateMeasures: [],
  defaultMeasures: [{ op: "count", field: null, input: null }],
  query: testResourceQuery({
    fields: {
      id: testQueryField("id", {
        scalar: "ID", nullable: false,
        filter: { field: "id", scalar: "String", values: [], operators: ["exact", "ne", "inList", "notInList", "isNull"] },
      }),
      name: testQueryField("name", {
        nullable: false, sort: { field: "name" },
        filter: { field: "name", scalar: "String", values: [], operators: [
          "exact", "ne", "gt", "gte", "lt", "lte", "inList", "notInList", "isNull", "contains", "iContains", "startsWith",
          "iStartsWith", "endsWith", "iEndsWith", "like", "iLike", "notLike", "notILike", "similar", "notSimilar", "iRegex",
        ] },
      }),
      is_default: testQueryField("is_default", {
        scalar: "Boolean", nullable: false, sort: { field: "is_default" },
        filter: { field: "is_default", scalar: "Boolean", values: [], operators: ["exact", "ne", "isNull"] },
      }),
      ...Object.fromEntries(["display_name", ...COLOR_FIELDS, "font_heading", "font_body", ...TEXT_FIELDS]
        .map((name) => [name, plainColumn(name)])),
      created_at: testQueryField("created_at", { scalar: "DateTime", nullable: false, filter: null, sort: { field: "created_at" } }),
      updated_at: testQueryField("updated_at", { scalar: "DateTime", nullable: false, filter: null, sort: { field: "updated_at" } }),
      logo: storageFile("logo"),
      logo_dark: storageFile("logo_dark"),
      contrast_warnings: testQueryField("contrast_warnings", { kind: "list", scalar: null, nullable: false, row: null, filter: null }),
    },
    axes: {
      is_default: testQueryAxis("is_default", {
        server: { input: "IS_DEFAULT", key: "is_default", labelInput: null, labelKey: null, valueMap: [] },
        drill: { kind: "value", field: "is_default", valueKey: "is_default", rangeKey: null, jsonPath: null, valueTransform: null, valueMap: [], nullMode: "isNull" },
      }),
    },
    sort: { default: [{ field: "is_default", direction: "DESC" }, { field: "name", direction: "ASC" }] },
  }),
});

interface ContrastWarningRow { seed: string; scheme: string; role: string }

export type KitRow = Record<(typeof COLOR_FIELDS)[number] | (typeof TEXT_FIELDS)[number], string> & {
  id: string; display_name: string; name: string; is_default: boolean;
  font_heading: string; font_body: string; logo: null; logo_dark: null;
  contrast_warnings: readonly ContrastWarningRow[]; created_at: string; updated_at: string;
};

const SAVED_AT = "2026-11-24T09:30:00Z";

/** The shipped kit (`resources/install/010_brandkit.brandkit.yaml`), which the first load makes the default. */
export const ANGEE: KitRow = {
  id: "bkt_angee", display_name: "Angee", name: "Angee", is_default: true,
  brand: "#e7b008", accent: "#6366f1", neutral: "#71717a", canvas: "#fafaf9", surface: "#ffffff", rail: "#09090b",
  success: "#22c55e", warning: "#eab308", danger: "#dc2626", info: "#4f46e5",
  font_heading: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
  font_body: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
  logo: null, logo_dark: null,
  footer_organization: "Angee", footer_address: "",
  voice: "Plain and short. Says what happened and what to do next. No exclamation marks.",
  contrast_warnings: [],
  created_at: "2026-11-02T09:00:00Z", updated_at: "2026-11-02T09:00:00Z",
};

/**
 * A second kit. Its warnings are what `BrandKit.contrast_warnings` returns for these colours: light
 * mail darkens the muted text far to read on the grey card, and the pale brand far to read as a link.
 */
export const SANDSTONE: KitRow = {
  id: "bkt_sandstone", display_name: "Sandstone", name: "Sandstone", is_default: false,
  brand: "#fde047", accent: "#0d9488", neutral: "#71717a", canvas: "#ffffff", surface: "#8a8580", rail: "#292524",
  success: "#16a34a", warning: "#d97706", danger: "#dc2626", info: "#2563eb",
  font_heading: "Georgia, 'Times New Roman', serif",
  font_body: "system-ui, sans-serif",
  logo: null, logo_dark: null,
  footer_organization: "Sandstone", footer_address: "1 Example Street\n12345 Example City",
  voice: "Warm and direct. Short sentences, concrete words.",
  contrast_warnings: [{ seed: "NEUTRAL", scheme: "LIGHT", role: "MUTED" }, { seed: "BRAND", scheme: "LIGHT", role: "LINK" }],
  created_at: "2026-11-10T14:00:00Z", updated_at: "2026-11-10T14:00:00Z",
};

export type KitColors = Pick<KitRow, (typeof COLOR_FIELDS)[number]>;

/** A kit's ten colours. */
export function colorsOf(kit: KitRow): KitColors {
  return Object.fromEntries(COLOR_FIELDS.map((name) => [name, kit[name]])) as KitColors;
}

/** A kit dressing the app, from the shipped kit's text facts: no contrast warning, since the app shows none. */
function appKit(id: string, name: string, colors: KitColors, isDefault = false): KitRow {
  return { ...ANGEE, id, name, display_name: name, is_default: isDefault, footer_organization: name, ...colors, contrast_warnings: [] };
}

/** A green kit whose ten colours all differ from the stock theme's. */
export const ORCHARD = appKit("bkt_orchard", "Orchard", {
  brand: "#3f7d20", accent: "#c2410c", neutral: "#6b665c", canvas: "#f7f5ee", surface: "#fffdf7", rail: "#1f2a1a",
  success: "#15803d", warning: "#b45309", danger: "#b91c1c", info: "#0e7490",
}, true);

/** A blue kit whose ten colours all differ from the stock theme's and from Orchard's. */
export const HARBOUR = appKit("bkt_harbour", "Harbour", {
  brand: "#1d4ed8", accent: "#0891b2", neutral: "#64748b", canvas: "#f1f5f9", surface: "#fbfdff", rail: "#0b1f3a",
  success: "#047857", warning: "#a16207", danger: "#be123c", info: "#4338ca",
});

/** Orchard with a brand too pale for white ink or for a link as stated. */
export const PALE = appKit("bkt_pale", "Pale", { ...colorsOf(ORCHARD), brand: "#fff59d" }, true);

/** The default kit's colours, as the public `default_brand_kit_colors` root answers them. */
export function defaultKitColors(kits: readonly KitRow[]): KitColors | null {
  const kit = kits.find((row) => row.is_default);
  return kit ? colorsOf(kit) : null;
}

/** `_GENERIC` in `brandkit/fields.py`: a font stack must close with a generic family. */
const FONT_STACK_REFUSAL = "End the font stack with a generic family, such as sans-serif.";
/** `DEFAULT_UNTICK_MESSAGE` in `brandkit/models.py`. */
const DEFAULT_UNTICK_REFUSAL = "This is the default kit: make another kit the default instead.";

/** The console's answer to a model validation failure (`_apply_validation_error` in `angee/graphql/schema.py`). */
function validationRefusal(field: string, message: string): GraphQLError {
  return new GraphQLError(message, {
    path: ["update_brand_kits_by_pk"],
    extensions: { code: "VALIDATION", validationErrors: { [field]: [message] }, formErrors: [] },
  });
}

/**
 * The console's kit roots over kits kept across saves. They stand in for the server's one-default
 * rule: a kit saved as the default takes the flag from the others, the default cannot be unticked,
 * and the list reads the default first. They do not work out contrast warnings for edited colours.
 * `refuseFontStack` refuses every save that writes the body font, as the console refuses a stack
 * with no closing generic family.
 */
export function brandKitWire(initial: readonly KitRow[], { refuseFontStack = false }: { refuseFontStack?: boolean } = {}): {
  /** The kits as the last save left them. */
  kits: () => readonly KitRow[];
  operations: Readonly<Record<string, GraphQLWireOperation>>;
} {
  let kits = initial;
  const kit = (id: unknown) => kits.find((row) => row.id === id) ?? null;
  const takeDefault = (id: string) => { kits = kits.map((row) => row.id === id ? row : { ...row, is_default: false }); };
  return {
    kits: () => kits,
    operations: {
      brand_kits: () => [...kits].sort((left, right) => Number(right.is_default) - Number(left.is_default) || left.name.localeCompare(right.name)),
      brand_kits_aggregate: () => ({ aggregate: { count: kits.length } }),
      brand_kits_by_pk: ({ id }) => kit(id),
      update_brand_kits_by_pk: ({ pk_columns, _set }) => {
        const { id } = pk_columns as { id: string };
        const changes = _set as Partial<KitRow>;
        const current = kit(id);
        if (!current) throw new GraphQLError("The kit does not exist.", { path: ["update_brand_kits_by_pk"] });
        if (refuseFontStack && "font_body" in changes) throw validationRefusal("font_body", FONT_STACK_REFUSAL);
        if (current.is_default && changes.is_default === false) throw validationRefusal("is_default", DEFAULT_UNTICK_REFUSAL);
        if (changes.is_default) takeDefault(id);
        kits = kits.map((row) => row.id === id
          ? { ...row, ...changes, display_name: changes.name ?? row.name, updated_at: SAVED_AT } : row);
        return kit(id);
      },
      insert_brand_kits_one: ({ object }) => {
        // The form sends every required field; the mail footer and voice default to blank.
        const values = object as Partial<KitRow>;
        const created = {
          footer_organization: "", footer_address: "", voice: "", ...values,
          id: "bkt_created", display_name: values.name ?? "", logo: null, logo_dark: null,
          is_default: Boolean(values.is_default) || !kits.some((row) => row.is_default),
          contrast_warnings: [], created_at: SAVED_AT, updated_at: SAVED_AT,
        } as KitRow;
        kits = [...kits, created];
        if (created.is_default) takeDefault(created.id);
        return created;
      },
    },
  };
}
