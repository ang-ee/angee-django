import { composeAddons } from "@angee/app";
import type { DocumentType } from "@angee/gql/console";
import type { ActionResult, DeletePreview } from "@angee/gql/console/actions";
import { defineAngeeSchemaMetadata, type DataResourceMetadata, type Row } from "@angee/metadata";
import {
  GraphQLError,
  resourceSnapshotOperations,
  storySchema,
  type GraphQLWireOperation,
  type GraphQLWireRequest,
} from "@angee/storybook/testing";
import { baseIcons, createRouteHref, defaultWidgets, type AppRuntime, type ChatterViewContext } from "@angee/ui";

import type { MessageTemplateBacklinks, MessageTemplatePreview } from "./documents.console";
import recordedFixtures from "./fixtures.recorded.json";
import messagingTemplates from "./index";
import {
  BRAND_KIT_MODEL, MESSAGE_TEMPLATE_MODEL, MESSAGE_TEMPLATES_PRESENCE, MESSAGE_TEMPLATES_ROUTE,
} from "./resources";

/**
 * The message templates console's workshop and test fixture, over refine's GraphQL
 * wire fake.
 *
 * Every value the server words or decides is in `fixtures.recorded.json`, which
 * `tests/test_messaging_templates_console_fixture.py` records from the framework's
 * test host and checks for drift; re-record it with the command in `docs/checks.md`,
 * never edit it. The recorded shapes the console's own documents read are typed from
 * their generated results, so a schema change that moves them fails the type-check.
 *
 * The fake answers from those recordings and keeps the library across requests: a
 * verb applies what the recording says it changed. A save applies its patch as it
 * is. A read or a verb the recording has no answer for is answered with a GraphQL
 * error naming it. Lists, counts and groups are `resourceSnapshotOperations` over the
 * current rows, so filters and grouping are the resource metadata's own.
 */

type WireRow = Row & { id: string };
type Preview = DocumentType<typeof MessageTemplatePreview>["message_template_preview"];
type Backlinks = NonNullable<DocumentType<typeof MessageTemplateBacklinks>["message_templates_by_pk"]>["backlinks"];

interface RecordedChanges {
  /** New rows whole, first; then each changed row as its id and the fields that changed. */
  rows: readonly WireRow[];
  removed: readonly string[];
  previews: Readonly<Record<string, Preview>>;
  backlinks: Readonly<Record<string, Backlinks>>;
}

interface RecordedError {
  message: string;
  path: readonly (string | number)[];
  extensions: { code?: string; formErrors?: readonly string[]; validationErrors?: Record<string, readonly string[]> };
}

type RecordedInsert =
  | { object: Readonly<Record<string, string>>; changes: RecordedChanges }
  | { object: Readonly<Record<string, string>>; error: RecordedError };

/** The names the stories and tests open rows by, as the recorder names them (`OPENED`). */
type OpenedRow = "welcome" | "welcomeArabic" | "passwordReset" | "invoiceSent" | "button" | "quote" | "recordCard"
  | "notice";

interface RecordedFixtures {
  metadata: Readonly<Record<string, unknown>>;
  opened: Readonly<Record<OpenedRow, string>>;
  rows: readonly WireRow[];
  member: Readonly<Record<string, Partial<Row>>>;
  kits: readonly WireRow[];
  backlinks: Readonly<Record<string, Backlinks>>;
  previews: Readonly<Record<string, Preview>>;
  previewsInKit: Readonly<Record<string, Readonly<Record<string, Preview>>>>;
  delete: Readonly<Record<string, { preview: DeletePreview; changes?: RecordedChanges }>>;
  override: Readonly<Record<string, { result: ActionResult; changes: RecordedChanges }>>;
  keep: Readonly<Record<string, { result: ActionResult; changes: RecordedChanges }>>;
  insert: readonly RecordedInsert[];
  save: readonly { id: string; set: Readonly<Record<string, unknown>>; error: RecordedError }[];
}

/** What the recorder wrote: JSON, so its values are checked by the recorder's own test, its shapes by these types. */
export const RECORDED = recordedFixtures as unknown as RecordedFixtures;

function recordedResource(model: string): DataResourceMetadata {
  const resource = defineAngeeSchemaMetadata({ angee: { resources: [RECORDED.metadata[model]] } }).angee?.resources?.[0];
  if (!resource) throw new Error(`The recorded fixtures carry no ${model} resource.`);
  return resource;
}

/** The console resources for message templates and brand kits, as the recorded metadata states them. */
export const MESSAGE_TEMPLATE_RESOURCE = recordedResource(MESSAGE_TEMPLATE_MODEL);
const BRAND_KIT_RESOURCE = recordedResource(BRAND_KIT_MODEL);

/** The recorded rows the stories and tests open, by what each is (the recorder's `OPENED` says which). */
export const ROW = RECORDED.opened;

/** The answer to Keep override on a row that overrides no shipped default, as the console words it. */
export const KEEP_REFUSAL: ActionResult = (() => {
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
  /** Keep override is refused as the console refuses it for a row that overrides no shipped default. */
  refuseKeep?: boolean;
  /**
   * The server says no row admits the reader to Override (`can_override` false), though the reader may still
   * preview every row. The permission schema has no such reader today, so this one answer is the fake's.
   */
  overrideDenied?: boolean;
  onRequest?: (request: GraphQLWireRequest) => void;
  onCall?: (call: MessageTemplateFixtureCall) => void;
}

function recordedError(error: RecordedError): GraphQLError {
  return new GraphQLError(error.message, { path: error.path, extensions: error.extensions });
}

/** The answer to a read or a verb the recording holds none for: a GraphQL error, which the client does not retry. */
function unrecorded(what: string): GraphQLError {
  return new GraphQLError(`The recorded fixtures hold no answer for ${what}.`);
}

function sameObject(left: Readonly<Record<string, unknown>>, right: unknown): boolean {
  if (typeof right !== "object" || right === null) return false;
  const other = right as Record<string, unknown>;
  const keys = Object.keys(left);
  return keys.length === Object.keys(other).length && keys.every((key) => left[key] === other[key]);
}

/** The schemas a story or a test mounts: the console over the fake, with the recorded metadata. */
export function messageTemplateSchemas({
  viewer = "admin", refuseSave = false, refuseKeep = false, overrideDenied = false, onRequest, onCall,
}: MessageTemplateFixtureOptions = {}) {
  const member = viewer === "member";
  let rows: WireRow[] = RECORDED.rows
    .map((row) => (member ? { ...row, ...RECORDED.member[row.id], id: row.id } : row))
    .map((row) => (overrideDenied ? { ...row, can_override: false } : row));
  const previews: Record<string, Preview> = member ? {} : { ...RECORDED.previews };
  const backlinks: Record<string, Backlinks> = member ? {} : { ...RECORDED.backlinks };
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
    const operation = resourceSnapshotOperations(
      [MESSAGE_TEMPLATE_RESOURCE, BRAND_KIT_RESOURCE],
      { [MESSAGE_TEMPLATE_MODEL]: rows, [BRAND_KIT_MODEL]: RECORDED.kits },
    )[root];
    if (!operation) throw unrecorded(root);
    return operation(args, request);
  };
  const operations: Record<string, GraphQLWireOperation> = {
    message_templates: snapshot("message_templates"),
    message_templates_aggregate: snapshot("message_templates_aggregate"),
    message_templates_groups: snapshot("message_templates_groups"),
    message_templates_groups_count: snapshot("message_templates_groups_count"),
    brand_kits: snapshot("brand_kits"),
    brand_kits_aggregate: snapshot("brand_kits_aggregate"),
    message_templates_by_pk: ({ id }) => {
      const row = find(id);
      if (!row) return null;
      return Object.hasOwn(backlinks, row.id) ? { ...row, backlinks: backlinks[row.id] } : row;
    },
    message_template_preview: ({ template, brand_kit }) => {
      const preview = typeof brand_kit === "string"
        ? (member ? undefined : RECORDED.previewsInKit[brand_kit]?.[String(template)])
        : previews[String(template)];
      if (!preview) throw unrecorded(`the preview of ${String(template)} in ${String(brand_kit ?? "the default kit")}`);
      return preview;
    },
    override_message_template: ({ template }) => {
      const entry = RECORDED.override[String(template)];
      if (!entry) throw unrecorded(`Override on ${String(template)}`);
      apply(entry.changes);
      return entry.result;
    },
    keep_message_template_override: ({ template }) => {
      if (refuseKeep) return KEEP_REFUSAL;
      const entry = RECORDED.keep[String(template)];
      if (!entry) throw unrecorded(`Keep override on ${String(template)}`);
      if (entry.result.ok) apply(entry.changes);
      return entry.result;
    },
    insert_message_templates_one: ({ object }) => {
      const recorded = RECORDED.insert.find((candidate) => sameObject(candidate.object, object));
      if (!recorded) throw unrecorded(`the insert of ${JSON.stringify(object)}`);
      if ("error" in recorded) throw recordedError(recorded.error);
      apply(recorded.changes);
      return recorded.changes.rows[0];
    },
    update_message_templates_by_pk: ({ pk_columns, _set }) => {
      const { id } = pk_columns as { id: string };
      const refusal = RECORDED.save[0];
      if (refuseSave && refusal) throw recordedError(refusal.error);
      const current = find(id);
      if (!current) throw unrecorded(`the row ${id}`);
      const saved: WireRow = { ...current, ...(_set as Row), id };
      rows = rows.map((row) => (row.id === id ? saved : row));
      return saved;
    },
    message_templates_delete: ({ id, confirm }) => {
      const entry = RECORDED.delete[String(id)];
      if (!entry) throw unrecorded(`Delete on ${String(id)}`);
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
    console: { ...fixture, metadata: { angee: { resources: [MESSAGE_TEMPLATE_RESOURCE, BRAND_KIT_RESOURCE] } } },
  };
}

/** The page's route hrefs, from the addon's own routes. */
export const MESSAGE_TEMPLATE_ROUTE_HREF = createRouteHref(messagingTemplates.routes ?? []);
export const MESSAGE_TEMPLATE_RECORD_ROUTE = `${MESSAGE_TEMPLATES_ROUTE}.record`;

/** The aside's route for a message template record, which the app derives from the route and the metadata. */
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
