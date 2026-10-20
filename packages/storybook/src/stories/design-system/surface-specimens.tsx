import { useState } from "react";
import * as U from "@angee/ui";
import { OAuthCallback, PassthroughChrome, UsernamePasswordForm } from "@angee/app";
import { themes } from "@angee/theme-angee/themes";
import { ResourceQuery } from "@angee/metadata";
import type { TypedDocumentNode } from "@angee/refine";
import { gql } from "@angee/ui/lib/gql";
import { RuntimeFixture, jsonResponse, storySchema, testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "../runtime-fixtures";

const noop = () => undefined;
const formSpec = { type: "object", properties: { title: { type: "string", label: "Title" } } };
const choices = [{ key: "local", category: "Local", defaults: { title: "Local record" }, config_schema: formSpec }];
const record = { id: "library-note", title: "Project handbook", owner: "Alex Morgan", permissions: ["read", "write"] };
const titleField = { name: "title", label: "Title", widget: "text", title: true } satisfies U.FormField;
const ownerField = { name: "owner", label: "Owner", widget: "text" } satisfies U.FormField;
const fields = [titleField, ownerField];
const resource = testDataResource("notes.Note", {
  schemaName: "public",
  roots: { list: "notes", detail: "note", aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteFilter", order: "NoteOrder" },
  fields: [...["title", "owner"].map(name => ({
    name, kind: "scalar" as const, scalar: "String", readable: true, aggregatable: false,
    creatable: true, updatable: true, requiredOnCreate: false,
  })), { name: "trashed", kind: "scalar", scalar: "Boolean", trashable: true,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  query: testResourceQuery({
    fields: { title: testQueryField("title"), owner: testQueryField("owner") },
    axes: { owner: testQueryAxis("owner", {
      server: { input: "owner", key: "owner" },
      drill: { kind: "value", field: "owner", valueKey: "owner", nullMode: "isNull", valueMap: [] },
    }) },
  }),
});
const schemas = storySchema(async () => jsonResponse({ data: {
  note: record,
  notes: [record],
  notes_aggregate: { aggregate: { count: 1 } },
  libraryPage: { rows: [{ ...record, parent: null, expandable: false }], total: 1 },
  action_selection_limit: 100,
  impl_choices: choices,
} }));
const publicSchema = schemas.public;
if (!publicSchema) throw new Error("The library fixture requires its public schema.");
publicSchema.metadata = { angee: { resources: [resource] } };
const source = { record, values: record };
const containers = U.containersFromChildren(U.FORM_CONTAINERS, {
  "notes.Note#rail": { "library.properties": { content:
    <U.RecordRailGroup id="properties" label="Properties" fields={[{ field: ownerField }]} />,
  } },
});
const formRuntime = { containers };

export function Forms() {
  return <RuntimeFixture schemas={schemas} runtime={formRuntime}>
    <U.Form resource="notes.Note" id={record.id} acknowledgedSource={source}
      formExtras={({ form }) => <U.SurfacePanel title="Bound values"><div className="grid gap-3 p-4">
        <BoundSummary form={form} />
        <U.BoundDescriptorField form={form} resource="notes.Note" field={{ name: "owner", widget: "text", label: "Owner" }} />
        <U.BoundFormValue form={form} name="title">{bound =>
          <U.Input aria-label="Bound title" value={String(bound.value ?? "")} onChange={event => bound.onChange(event.target.value)} onBlur={bound.onCommit} />
        }</U.BoundFormValue>
      </div></U.SurfacePanel>}>
      <U.Field {...titleField} />
      <U.Group label="Details"><U.Field {...ownerField} /></U.Group>
      <U.Action id="review" label="Review" run={async () => undefined} />
      <U.Tab id="notes" label="Notes"><U.EmptyState title="Notes" description="Content supplied through the tab declaration." /></U.Tab>
    </U.Form>
  </RuntimeFixture>;
}
function BoundSummary({ form }: { form: Parameters<typeof U.useFormViewValues>[0] }) {
  const title = U.useFormViewValues<string>(form, "title");
  return <U.Chip>{title}</U.Chip>;
}

export function Resources() {
  return <RuntimeFixture schemas={schemas}><div className="grid gap-6">
    <U.ResourceCreate resource="notes.Note" fields={fields} defaultValues={record} title="Create a record" />
    <U.ResourceEdit resource="notes.Note" id={record.id} fields={fields} acknowledgedSource={source} />
    <U.ResourceShow resource="notes.Note" id={record.id} fields={fields} acknowledgedSource={source} />
  </div></RuntimeFixture>;
}

export function Collections() {
  return <RuntimeFixture schemas={schemas}><div className="grid gap-6">
    <U.List resource="notes.Note" presentation="embedded">
      <U.Column field="title" header="Title" /><U.Column field="owner" header="Owner" />
      <U.Facet field="owner" label="Owner" />
    </U.List>
    <U.DrawerResourceList resource="notes.Note" presentation="embedded" hideCreate
      columns={[{ field: "title", header: "Title" }, { field: "owner", header: "Owner" }]} fields={["title", "owner"]} />
    <U.AggregatePanel resource="notes.Note" title="Records" dimensions={[]} />
    <U.RemovedRecords resource={resource} label={row => String(row.title)} fields={["title"]} />
  </div></RuntimeFixture>;
}

type TreeRecord = typeof record & { parent: string | null; expandable: boolean };
const treeDocument: TypedDocumentNode<{ libraryPage: U.CollectionPage<TreeRecord> }, Record<string, never>> = gql`
  query LibraryTree {
    libraryPage { total rows { expandable id parent title } }
  }
`;
const treeSource: U.CollectionSource<TreeRecord> = {
  query: ResourceQuery.forRows({ fields: { id: { scalar: "ID" }, title: { scalar: "String" } } }),
  rows: U.collectionQuery({ document: treeDocument, variables: () => ({}), select: data => data.libraryPage }),
};
export function CollectionTree() {
  return <RuntimeFixture schemas={schemas}><U.ResourceViewProvider scope="local">
    <U.CollectionTreeView resource="library.Tree" source={treeSource} rowKey="id" parent="parent" label="title"
      hasChildren="expandable" columns={[{ field: "title", header: "Title" }]} presentation="embedded" />
  </U.ResourceViewProvider></RuntimeFixture>;
}

const points = [{ key: "ready", label: "Ready", value: 12 }, { key: "review", label: "In review", value: 5 }];
export function Charts() {
  return <U.DashboardView>
    <U.Metric label="Records" value={17} format="count" icon="file" />
    <U.Metric label="Ready" value={12} format="count" tone="success" />
    <div className="grid gap-6 md:grid-cols-2">
      <div className="h-48"><U.DashboardBars title="Records by state" points={points} /></div>
      <div className="h-48"><U.DashboardDonut title="State share" points={points} /></div>
    </div>
    <U.TimelineView rows={[{ ...record, date: "2026-10-19T12:00:00Z" }]} dateField="date" titleField="title" bodyField="owner" />
  </U.DashboardView>;
}

export function Editors() {
  const [value, setValue] = useState<unknown>({ title: "Handbook", published: false });
  const [rows, setRows] = useState<U.RowsValue>([{ title: "First item" }]);
  const [date, setDate] = useState<Date | null>(new Date("2026-10-19T12:00:00Z"));
  const [open, setOpen] = useState(false);
  const rowsField = { name: "items", label: "Items", rowTemplate: [{ name: "title", label: "Title", widget: "text" }] };
  return <RuntimeFixture schemas={schemas}><div className="grid gap-4">
    <U.JsonEditor value={value} onChange={setValue} /><U.JsonValueView value={value} />
    <U.RowsField value={rows} onChange={setRows} field={rowsField} />
    <U.DatePopover selected={date} label={date?.toISOString().slice(0, 10) ?? "Choose date"} ariaLabel="Review date"
      open={open} onOpenChange={setOpen} onSelectDate={next => { setDate(next); setOpen(false); }} />
    <U.VisibilityControl value="team" options={[{ value: "team", label: "Team" }, { value: "private", label: "Private" }]}
      allowedValues={["team", "private"]} onSelect={async () => undefined} />
    <U.InlineTextAction value="Handbook" inputLabel="Rename handbook" onSubmit={noop}
      renderTrigger={({ open }) => <U.Button onClick={open}>Rename handbook</U.Button>} />
  </div></RuntimeFixture>;
}

export function ActionForms() {
  const [open, setOpen] = useState(false);
  return <RuntimeFixture schemas={schemas}>
    <U.Button onClick={() => setOpen(true)}>Open action form</U.Button>
    <U.ActionFormDialog open={open} onOpenChange={setOpen} context={{ record, selectedIds: [record.id] }}
      action={{ id: "rename", label: "Rename", args: [{ name: "title", label: "Title", kind: "string" }],
        submit: async () => ({ ok: true, message: "Renamed" }) }} />
  </RuntimeFixture>;
}

const previewImage = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120"><rect width="320" height="120" fill="#64748b"/><text x="160" y="68" text-anchor="middle" fill="white" font-size="24">Preview fixture</text></svg>')}`;
export function Previews() {
  return <div className="grid gap-4 md:grid-cols-2">
    <U.ImagePreviewSurface src={previewImage} alt="Preview fixture" />
    <U.PreviewPane file={{ url: previewImage, name: "fixture.svg", mime: "image/svg+xml" }} />
  </div>;
}

const themeDefinition = themes[0];
if (!themeDefinition) throw new Error("The library fixture requires the Angee theme.");
export function ThemeSurfaces() {
  const appearance = U.useAppearance();
  const [value, setValue] = useState<U.ThemeOptionsEditorProps["value"]>();
  return <div className="grid gap-6">
    <U.ThemeCustomizationEditor definition={themeDefinition} value={value} onChange={setValue} />
    <U.ThemePreviewFrame title="Live theme preview" themeId={appearance.effectiveThemeId} colorScheme={appearance.colorScheme} />
    <U.ThemeSpecimenSurface />
  </div>;
}

export function Authentication() {
  return <PassthroughChrome><U.SurfacePanel title="Sign in">
    <UsernamePasswordForm />
  </U.SurfacePanel></PassthroughChrome>;
}

export function Callback() {
  return <OAuthCallback redirectUri="/callback" fallbackRedirect="/" complete={async () => ({ ok: false, error: "Example failure" })}
    copy={{ pendingTitle: "Completing sign in", pendingBody: "Please wait.", errorTitle: "Unable to sign in",
      backHref: "#sign-in", backLabel: "Return to sign in", serverError: "Browser required",
      missingInfo: "This specimen has no authorization code.", failure: "Try again." }} />;
}

function HookValues() {
  const limit = U.useActionSelectionLimit();
  const options = U.useEnumOptions("notes.Note", "owner");
  const impl = U.useImplChoices("notes.Note", "implementation");
  const config = U.useImplConfigFields("notes.Note", "implementation", choices);
  const category = U.useImplCategory("notes.Note", "implementation");
  const prefill = U.useImplPrefill("notes.Note", "implementation", {}, choices);
  const descriptors = U.useFormSpecFields(formSpec);
  const [ids] = U.useListIdentities(2);
  const routeRecord = U.useRouteRecordId();
  const actions = U.useTrashActions(resource);
  const rowActions = U.useTrashRowActions(resource);
  const [, outcome] = U.useActionOutcomeMutation("library_action");
  const [, result] = U.useActionResultMutation("library_action");
  const [, action] = U.useRecordActionMutation("library_action");
  const [, chromeMutation] = U.useRecordChromeActionMutation("library_action");
  const [, chromeOutcome] = U.useRecordChromeActionOutcome("library_action");
  const run = U.useRecordAction(async () => "Reviewed");
  const [reviewed, setReviewed] = useState(false);
  return <div className="grid gap-4">
    <U.MetaGrid rows={[
      ["Action selection limit", limit.limit ?? "Loading"],
      ["Choice options", options.length], ["Implementation choices", impl.length],
      ["Implementation category", category("local") || "Loading"],
      ["Config fields", config.fields.length], ["Prefill", String(prefill("local")?.title ?? "")],
      ["Form descriptors", descriptors.length], ["Stable row identities", ids.current.length],
      ["Route record", routeRecord ?? "Collection route"], ["Trash actions", actions.length],
      ["Trash row actions", rowActions.length],
      ["Action bindings", [action, chromeMutation, chromeOutcome, outcome, result].some(state => state.fetching) ? "Pending" : "Ready"],
    ]} />
    <U.Button onClick={() => void run({ record, values: {}, refresh: () => setReviewed(true),
      update: async () => record, prompt: async () => null })}>{reviewed ? "Reviewed" : "Review record"}</U.Button>
  </div>;
}
export function Hooks() {
  return <RuntimeFixture schemas={schemas}>
    <U.RecordChromeProvider value={{ resource: "notes.Note", canonicalResource: "notes.Note", dataProviderName: "public", recordId: record.id, record, formReadOnly: false }}>
      <HookValues />
    </U.RecordChromeProvider>
  </RuntimeFixture>;
}
