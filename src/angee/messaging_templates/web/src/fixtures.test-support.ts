import { composeAddons } from "@angee/app";
import { defineAngeeSchemaMetadata, type DataResourceMetadata, type Row } from "@angee/metadata";
import {
  GraphQLError,
  resourceSnapshotOperations,
  storySchema,
  type GraphQLWireOperation,
  type GraphQLWireRequest,
} from "@angee/storybook/testing";
import { baseIcons, createRouteHref, defaultWidgets, type AppRuntime, type ChatterViewContext } from "@angee/ui";

import messagingTemplates from "./index";
import { MESSAGE_TEMPLATE_MODEL, MESSAGE_TEMPLATES_PRESENCE, MESSAGE_TEMPLATES_ROUTE } from "./resources";
import recordedFixtures from "./fixtures.recorded.json";

/**
 * The message templates console's workshop and test fixture, over refine's GraphQL
 * wire fake.
 *
 * Every value the server words or decides is in `fixtures.recorded.json`, which a
 * scratch script records from the framework's bare test host and writes whole; it
 * is regenerated, never edited (the command is in the lane report that added it):
 * the console resource metadata of `messaging_templates.MessageTemplate` as a host
 * enabling the addon alone emits it; the library's rows as an administrator reads
 * them and what a member reads differently; each row's backlinks, preview (in the
 * default kit, and Welcome's in the other kit) and delete preview; the brand kits;
 * and what Override, Keep override, Delete, the insert and a refused save answer,
 * with what each changed in the library. Public ids are stable aliases of a row's
 * kind, key, language and whether it is shipped.
 *
 * The fake answers from those recordings and keeps the library across requests:
 * a verb applies the changes recorded for it. Two things it stands in for rather
 * than recorded: a save applies its patch as it is, and an insert of an identity
 * the recording has no answer for starts as the recorded new row of its kind
 * (what a key with no row at all starts as), with no preview. Lists, counts and
 * groups are `resourceSnapshotOperations` over the current rows, so filters and
 * grouping are the resource metadata's own.
 */

type WireRow = Row & { id: string };

interface RecordedPreview {
  rendered: { subject: string; preheader: string; html: string; plain_text: string; language: string } | null;
  warnings: readonly { code: string; detail: string }[];
}

interface RecordedChanges {
  /** New rows whole, first; then each changed row as its id and the fields that changed. */
  rows: readonly WireRow[];
  removed: readonly string[];
  previews: Readonly<Record<string, RecordedPreview>>;
  backlinks: Readonly<Record<string, readonly WireRow[] | null>>;
}

interface RecordedOutcome {
  ok: boolean;
  message: string;
  code: string | null;
  validation_errors: unknown;
  id: string | null;
}

interface RecordedError {
  message: string;
  path: readonly (string | number)[];
  extensions: { code?: string; formErrors?: readonly string[]; validationErrors?: Record<string, readonly string[]> };
}

type RecordedInsert =
  | { object: Readonly<Record<string, string>>; changes: RecordedChanges }
  | { object: Readonly<Record<string, string>>; error: RecordedError };

interface RecordedFixtures {
  metadata: unknown;
  kits: readonly { id: string; name: string; is_default: boolean }[];
  rows: readonly WireRow[];
  member: { rows: Readonly<Record<string, Partial<Row>>>; backlinks: Readonly<Record<string, null>> };
  backlinks: Readonly<Record<string, readonly WireRow[] | null>>;
  previews: Readonly<Record<string, RecordedPreview>>;
  previewsInKit: Readonly<Record<string, Readonly<Record<string, RecordedPreview>>>>;
  delete: Readonly<Record<string, { preview: Row & { has_blockers: boolean; refusals: readonly string[] }; changes?: RecordedChanges }>>;
  override: Readonly<Record<string, { result: RecordedOutcome; changes: RecordedChanges }>>;
  keep: Readonly<Record<string, { result: RecordedOutcome; changes: RecordedChanges }>>;
  insert: readonly RecordedInsert[];
  save: readonly { id: string; set: Readonly<Record<string, unknown>>; error: RecordedError }[];
}

/** What the recording script wrote. */
export const RECORDED = recordedFixtures as unknown as RecordedFixtures;

/** The console resource for `messaging_templates.MessageTemplate`, as the recorded metadata states it. */
export const MESSAGE_TEMPLATE_RESOURCE: DataResourceMetadata = (() => {
  const resource = defineAngeeSchemaMetadata({ angee: { resources: [RECORDED.metadata] } }).angee?.resources?.[0];
  if (!resource) throw new Error("The recorded fixtures carry no message template resource.");
  return resource;
})();

/** The recorded rows the stories and tests open, by what each is. */
export const ROW = {
  /** A shipped template nobody overrides. */
  welcome: "mtl_template_example_welcome_en_shipped",
  /** The deployment's own Arabic translation of it. */
  welcomeArabic: "mtl_template_example_welcome_ar",
  /** A current override of a shipped template, whose preview warns. */
  passwordReset: "mtl_template_example_password_reset_en",
  /** An override whose shipped default changed, and whose sample no longer renders. */
  invoiceSent: "mtl_template_example_invoice_sent_en",
  /** A shipped block every template includes. */
  button: "mtl_block_button_en_shipped",
  /** A current override of a shipped block nothing includes. */
  quote: "mtl_block_quote_en",
  /** An override of a shipped block whose default changed. */
  recordCard: "mtl_block_record_card_en",
  /** The deployment's own block, which an override includes. */
  notice: "mtl_block_notice_en",
} as const;

for (const id of Object.values(ROW)) {
  if (!RECORDED.rows.some((row) => row.id === id)) throw new Error(`The recorded fixtures have no row ${id}.`);
}

/** The answer to Keep override on a row whose shipped default is gone, as the console words it. */
export const KEEP_REFUSAL: RecordedOutcome = (() => {
  const refusal = Object.values(RECORDED.keep).find((entry) => !entry.result.ok)?.result;
  if (!refusal) throw new Error("The recorded fixtures carry no refused Keep override.");
  return refusal;
})();

export type MessageTemplateViewer = "admin" | "member";

/** One root field the fake answered, with the arguments it was asked with. */
export interface MessageTemplateFixtureCall {
  field: string;
  args: Readonly<Record<string, unknown>>;
}

export interface MessageTemplateFixtureOptions {
  /** A platform administrator, or a member who reads every row and may do nothing with it. */
  viewer?: MessageTemplateViewer;
  /** Every save is refused as the console refused the recorded one. */
  refuseSave?: boolean;
  /** Keep override is refused as the console refuses it for an override whose shipped default is gone. */
  refuseKeep?: boolean;
  onRequest?: (request: GraphQLWireRequest) => void;
  onCall?: (call: MessageTemplateFixtureCall) => void;
}

function recordedError(error: RecordedError): GraphQLError {
  return new GraphQLError(error.message, { path: error.path, extensions: error.extensions });
}

function sameObject(left: Readonly<Record<string, unknown>>, right: unknown): boolean {
  if (typeof right !== "object" || right === null) return false;
  const other = right as Record<string, unknown>;
  const keys = Object.keys(left);
  return keys.length === Object.keys(other).length && keys.every((key) => left[key] === other[key]);
}

/** The recorded new row of a kind, given the asked identity: what a key with no row at all starts as. */
function standInInsert(object: Readonly<Record<string, unknown>>): RecordedChanges {
  const kind = String(object.kind ?? "");
  const seeded = RECORDED.insert.find((entry): entry is Extract<RecordedInsert, { changes: RecordedChanges }> =>
    "changes" in entry && entry.object.kind === kind)?.changes.rows[0];
  if (!seeded) throw new Error(`The recorded fixtures have no new row of the kind ${kind}.`);
  const key = String(object.key ?? "");
  const language = String(object.language ?? "");
  const name = String(object.name ?? "");
  return {
    rows: [{ ...seeded, id: `mtl_${kind}_${key.replaceAll(".", "_")}_${language}`, key, language, name, display_name: name }],
    removed: [],
    previews: {},
    backlinks: {},
  };
}

/** The schemas a story or a test mounts: the console over the fake, with the recorded metadata. */
export function messageTemplateSchemas({
  viewer = "admin", refuseSave = false, refuseKeep = false, onRequest, onCall,
}: MessageTemplateFixtureOptions = {}) {
  const member = viewer === "member";
  let rows: WireRow[] = RECORDED.rows.map((row) => (member ? { ...row, ...RECORDED.member.rows[row.id], id: row.id } : row));
  const previews: Record<string, RecordedPreview> = member ? {} : { ...RECORDED.previews };
  const backlinks: Record<string, readonly WireRow[] | null> = member
    ? { ...RECORDED.member.backlinks }
    : { ...RECORDED.backlinks };
  const find = (id: unknown) => rows.find((row) => row.id === id) ?? null;
  const apply = (changes: RecordedChanges) => {
    for (const change of changes.rows) {
      rows = rows.some((row) => row.id === change.id)
        ? rows.map((row) => (row.id === change.id ? { ...row, ...change } : row))
        : [...rows, change];
    }
    rows = rows.filter((row) => !changes.removed.includes(row.id));
    Object.assign(previews, changes.previews);
    Object.assign(backlinks, changes.backlinks);
  };
  const snapshot = (root: string): GraphQLWireOperation => (args, request) => {
    const operation = resourceSnapshotOperations([MESSAGE_TEMPLATE_RESOURCE], { [MESSAGE_TEMPLATE_MODEL]: rows })[root];
    if (!operation) throw new Error(`The message template resource has no root ${root}.`);
    return operation(args, request);
  };
  const operations: Record<string, GraphQLWireOperation> = {
    message_templates: snapshot("message_templates"),
    message_templates_aggregate: snapshot("message_templates_aggregate"),
    message_templates_groups: snapshot("message_templates_groups"),
    message_templates_groups_count: snapshot("message_templates_groups_count"),
    message_templates_by_pk: ({ id }) => {
      const row = find(id);
      return row ? { ...row, backlinks: backlinks[row.id] ?? null } : null;
    },
    message_template_preview: ({ template, brand_kit }) => {
      const preview = typeof brand_kit === "string"
        ? (member ? undefined : RECORDED.previewsInKit[brand_kit]?.[String(template)])
        : previews[String(template)];
      if (!preview) throw new Error(`No preview of ${String(template)} in ${String(brand_kit ?? "the default kit")} is recorded for this reader.`);
      return preview;
    },
    brand_kits: () => RECORDED.kits,
    override_message_template: ({ template }) => {
      const entry = RECORDED.override[String(template)];
      if (!entry) throw new Error(`No override of ${String(template)} is recorded.`);
      apply(entry.changes);
      return entry.result;
    },
    keep_message_template_override: ({ template }) => {
      if (refuseKeep) return KEEP_REFUSAL;
      const entry = RECORDED.keep[String(template)];
      if (!entry) throw new Error(`No Keep override of ${String(template)} is recorded.`);
      if (entry.result.ok) apply(entry.changes);
      return entry.result;
    },
    insert_message_templates_one: ({ object }) => {
      const recorded = RECORDED.insert.find((candidate) => sameObject(candidate.object, object));
      if (recorded && "error" in recorded) throw recordedError(recorded.error);
      const changes = recorded && "changes" in recorded
        ? recorded.changes
        : standInInsert(object as Record<string, unknown>);
      apply(changes);
      return changes.rows[0];
    },
    update_message_templates_by_pk: ({ pk_columns, _set }) => {
      const { id } = pk_columns as { id: string };
      const refusal = RECORDED.save[0];
      if (refuseSave && refusal) throw recordedError(refusal.error);
      const current = find(id);
      if (!current) throw new Error(`No message template ${id} is recorded.`);
      const changes = _set as Row;
      const { format, name } = changes;
      // The server reads a choice column back in its member's name, and a row's display name is its name.
      const saved: WireRow = {
        ...current,
        ...changes,
        id,
        ...(typeof format === "string" ? { format: format.toUpperCase() } : {}),
        ...(typeof name === "string" ? { display_name: name } : {}),
      };
      rows = rows.map((row) => (row.id === id ? saved : row));
      return saved;
    },
    message_templates_delete: ({ id, confirm }) => {
      const entry = RECORDED.delete[String(id)];
      if (!entry) throw new Error(`No delete of ${String(id)} is recorded.`);
      if (confirm === true && !entry.preview.has_blockers && entry.changes) apply(entry.changes);
      return entry.preview;
    },
  };
  const answered = Object.fromEntries(Object.entries(operations).map(([field, operation]) => [
    field,
    ((args, request) => {
      onCall?.({ field, args });
      return operation(args, request);
    }) satisfies GraphQLWireOperation,
  ]));
  const fixture = storySchema(answered, onRequest).public!;
  return {
    public: fixture,
    console: { ...fixture, metadata: { angee: { resources: [MESSAGE_TEMPLATE_RESOURCE] } } },
  };
}

/** The page's route hrefs, from the addon's own routes. */
export const MESSAGE_TEMPLATE_ROUTE_HREF = createRouteHref(messagingTemplates.routes ?? []);
export const MESSAGE_TEMPLATE_RECORD_ROUTE = `${MESSAGE_TEMPLATES_ROUTE}.record`;

/** The aside's route for a message template record, as the composed app derives it from the route and metadata. */
const RECORD_CHATTER_ROUTE = {
  name: MESSAGE_TEMPLATE_RECORD_ROUTE,
  path: "/message-templates/$id",
  viewType: MESSAGE_TEMPLATE_RESOURCE.resourceType ?? MESSAGE_TEMPLATE_MODEL,
  resourceType: MESSAGE_TEMPLATE_RESOURCE.resourceType,
  modelLabel: MESSAGE_TEMPLATE_MODEL,
  canonicalLabel: MESSAGE_TEMPLATE_MODEL,
  recordEdges: MESSAGE_TEMPLATE_RESOURCE.recordEdges ?? [],
  history: MESSAGE_TEMPLATE_RESOURCE.history,
  recordParam: "id",
};

/** The addon's own containers and presets, composed as the app composes them. */
const COMPOSED = composeAddons([{
  id: messagingTemplates.id,
  containers: messagingTemplates.containers,
  resourceViews: messagingTemplates.resourceViews,
}], { canonicalModelLabel: (model) => model });

function runtimeFor(viewer: MessageTemplateViewer): Partial<AppRuntime> {
  return {
    routeHref: MESSAGE_TEMPLATE_ROUTE_HREF,
    routesByResource: {
      [MESSAGE_TEMPLATE_MODEL]: {
        collection: MESSAGE_TEMPLATES_ROUTE,
        record: { name: MESSAGE_TEMPLATE_RECORD_ROUTE, param: "id" },
      },
    },
    widgets: defaultWidgets,
    icons: { ...baseIcons, ...messagingTemplates.icons },
    containers: COMPOSED.containers,
    resourceViews: COMPOSED.resourceViews,
    chatterRoutes: [RECORD_CHATTER_ROUTE],
    auth: {
      status: "authenticated",
      user: {
        id: `usr_${viewer}`,
        name: viewer === "admin" ? "Administrator" : "Member",
        permitted: viewer === "admin" ? [MESSAGE_TEMPLATES_PRESENCE] : [],
      },
      hasRole: () => false,
    },
  };
}

const RUNTIMES: Readonly<Record<MessageTemplateViewer, Partial<AppRuntime>>> = {
  admin: runtimeFor("admin"),
  member: runtimeFor("member"),
};

/** The runtime a story or a test mounts for one reader: the addon's routes, containers, presets and glyph. */
export function messageTemplateRuntime(viewer: MessageTemplateViewer = "admin"): Partial<AppRuntime> {
  return RUNTIMES[viewer];
}

/** The Links tab's view of one message template record. */
export function messageTemplateRecordContext(id: string): ChatterViewContext {
  return {
    pathname: MESSAGE_TEMPLATE_ROUTE_HREF(MESSAGE_TEMPLATE_RECORD_ROUTE, { id }),
    params: { id },
    route: RECORD_CHATTER_ROUTE,
    view: { kind: "record", type: RECORD_CHATTER_ROUTE.viewType, sqid: id },
  };
}
