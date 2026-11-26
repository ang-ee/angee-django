import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  ListView,
  RowsListView,
  type ListSearchDeclaration,
  defineRowAction,
  type ListColumn,
  type RowActionDeclaration,
} from "@angee/ui";

import { ResourceQuery } from "@angee/metadata";
import { backendOperations, backendResource } from "./implementation-fixtures";
import { storySchema, testDataResource, testQueryField, testResourceQuery } from "./runtime-fixtures";

const rows = [
  {
    id: "note-1",
    title: "Permission model notes",
    tags: ["architecture", "iam"],
    status: "ACTIVE",
    owner: "Alex",
    words: 1840,
    updatedAt: "2026-06-04T12:00:00Z",
  },
  {
    id: "note-2",
    title: "CSV import edge cases",
    tags: ["resources"],
    status: "DRAFT",
    owner: "Sofia",
    words: 920,
    updatedAt: "2026-06-03T15:00:00Z",
  },
  {
    id: "note-3",
    title: "Workspace lifecycle",
    tags: ["composer", "dev"],
    status: "ARCHIVED",
    owner: "Mina",
    words: 2460,
    updatedAt: "2026-05-28T09:30:00Z",
  },
] as const;

type StoryRow = (typeof rows)[number];

const columns = [
  { field: "title", header: "Title" },
  { field: "tags", header: "Tags", sortable: false },
  {
    field: "status",
    header: "Status",
    widget: "statusBadge",
    options: [
      { value: "ACTIVE", label: "Active" },
      { value: "DRAFT", label: "Draft" },
      { value: "ARCHIVED", label: "Archived" },
    ],
    tone: {
      ACTIVE: "success",
      DRAFT: "warning",
      ARCHIVED: "neutral",
    },
  },
  { field: "owner", header: "Owner" },
  { field: "words", header: "Words", align: "right" },
  { field: "updatedAt", header: "Updated", widget: "datetime" },
] satisfies readonly ListColumn<StoryRow>[];

const rowActions: readonly RowActionDeclaration<StoryRow>[] = [
  defineRowAction({
    kind: "page",
    id: "open-note",
    label: "Open note",
    icon: "pencil",
    variant: "ghost",
    primary: true,
    pendingPolicy: "disable-actions",
    onSelect: () => undefined,
  }),
];

// A handful of tasks, two pairs of them tied on rank, ordered in the browser by status, rank, then title.
const tasks = [
  { id: "task-1", title: "Draft the brief", status: "open", rank: 1 },
  { id: "task-2", title: "Book the room", status: "open", rank: 1 },
  { id: "task-3", title: "Collect quotes", status: "open", rank: 2 },
  { id: "task-4", title: "Send invites", status: "closed", rank: 3 },
  { id: "task-5", title: "Archive notes", status: "closed", rank: 1 },
  { id: "task-6", title: "Approve budget", status: "closed", rank: 3 },
] as const;
const taskFields = { id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" }, rank: { scalar: "Int" } } as const;
const taskQuery = ResourceQuery.forRows({ fields: taskFields }).contract;
const taskResource = testDataResource("notes.Task", {
  schemaName: "public", rowModel: "client", recordRepresentation: "title",
  roots: { list: "tasks", aggregate: "tasks_aggregate" }, typeNames: { filter: "TaskFilter", order: "TaskOrder" },
  fields: Object.entries(taskFields).map(([name, { scalar }]) => ({ name, kind: "scalar", scalar, readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
  query: { ...taskQuery, sort: { ...taskQuery.sort, default: (["status", "rank", "title"] as const).map((field) => ({ field, direction: "ASC" as const })) } },
});

const storySchemas = storySchema({
  notes: () => rows,
  notes_aggregate: () => ({ aggregate: { count: rows.length } }),
  tasks: () => tasks,
  tasks_aggregate: () => ({ aggregate: { count: tasks.length } }),
  ...backendOperations,
}, { resources: [testDataResource("notes.Note", {
  schemaName: "public",
  roots: { list: "notes", aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteFilter", order: "NoteOrder" },
  query: testResourceQuery({ fields: Object.fromEntries(([
    ["id", "ID"], ["title", "String"], ["tags", "String"], ["status", "String"],
    ["owner", "String"], ["words", "Int"], ["updatedAt", "DateTime"],
  ] as const).map(([name, scalar]) => [name, testQueryField(name, { scalar, sort: { field: name } })])) }),
}), taskResource, backendResource] });

const meta = {
  title: "Views/ListView",
  parameters: { angeeFixture: { schemas: storySchemas }, layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const VisibleFieldsChooser: Story = {
  render: () => <ListFixture />,
};

export const SearchShortcuts: Story = {
  render: () => <ListFixture withShortcuts />,
};

/**
 * Grouped by status and ordered by status, then rank, then title; open both groups to see their
 * rows. At load the Status header shows no sort (a group's tasks share their status), Rank shows
 * ascending (each group's rows follow it) and Title shows not sorted (it only breaks rank ties: Book
 * the room before Draft the brief). Press Title's sort button: Title sorts ascending as the only key,
 * Rank shows not sorted, and each group's rows follow their titles (Collect quotes moves above Draft
 * the brief); the status key goes too, so the groups no longer follow it.
 */
export const GroupedWithTieBreakers: Story = {
  name: "Grouped With Tie-breakers",
  render: () => (
    <div className="max-w-3xl">
      <ListView resource="notes.Task" defaultGroup={{ field: "status" }} columns={[
        { field: "status", header: "Status" }, { field: "rank", header: "Rank", align: "right" }, { field: "title", header: "Title" },
      ]} />
    </div>
  ),
};

/**
 * Two storage backends; Old archive stores an implementation key the registry no longer names, so its
 * Backend column reads null. Its cell says "Not registered" in the warning tone, where Team files'
 * shows its implementation, Local folder.
 */
export const ImplementationKeyNotRegistered: Story = {
  render: () => (
    <div className="max-w-3xl">
      <ListView resource="storage.Backend" columns={[{ field: "label", header: "Name" }, { field: "backend_class", header: "Backend" }]} />
    </div>
  ),
};

function ListFixture({ withShortcuts = false }: { withShortcuts?: boolean }) {
  return (
    <div className="max-w-5xl">
      <ListView
        resource="notes.Note"
        columns={columns}
        rowActions={rowActions}
        chrome={{ heading: { label: "Notes", hint: "Shared team records", audience: "Editors" } }}
        search={withShortcuts ? { shortcuts: [{ kind: "toggle", id: "status:ACTIVE" }, { kind: "facet", field: "status" }] } : undefined}
        filterOptions={withShortcuts ? [
          { id: "status:ACTIVE", label: "Active", filter: { status: { exact: "ACTIVE" } } },
        ] : undefined}
        customFilterFields={withShortcuts ? [{ id: "status", label: "Status", type: "selection", options: [
          { value: "ACTIVE", label: "Active" }, { value: "DRAFT", label: "Draft" }, { value: "ARCHIVED", label: "Archived" },
        ] }] : undefined}
        createLabel="New note"
        onCreate={() => undefined}
      />
    </div>
  );
}

const referenceQuery = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, "requester.display_name": { scalar: "String" },
  submitted: { scalar: "DateTime" }, due: { scalar: "Date" }, duration: { scalar: "Float" },
  priority: { kind: "enum", values: [{ value: "high", description: "High" }, { value: "low", description: "Low" }] },
  status: { kind: "enum", values: [{ value: "open", description: "Open" }, { value: "closed", description: "Closed" }] },
  owner: { kind: "relation" },
} });
const referenceSearch: ListSearchDeclaration = { shortcuts: [
  { kind: "text", field: "title" }, { kind: "text", field: "requester.display_name" },
  { kind: "clause", field: "submitted" }, { kind: "clause", field: "due" }, { kind: "clause", field: "duration" },
  { kind: "facet", field: "priority" }, { kind: "facet", field: "status" }, { kind: "toggle", id: "mine" },
] };

/** Eight separate controls and the trailing box, authored entirely as list options. */
export const ReferenceShortcuts: Story = { render: () => <ReferenceList /> };
export const ReferenceShortcutsFullBox: Story = { render: () => <ReferenceList box /> };

function ReferenceList({ box }: { box?: true }) {
  return <RowsListView
    scope="local"
    query={referenceQuery}
    rows={[{ id: "record-1", title: "Review the draft", requester: { display_name: "Lee" }, submitted: "2026-10-04T09:00:00Z",
      due: "2026-10-08", duration: 2, priority: "high", status: "open", owner: "viewer" }]}
    columns={[
      { field: "title", header: "Title" }, { field: "requester.display_name", header: "Filed by" },
      { field: "submitted", header: "Submitted", widget: "datetime" }, { field: "due", header: "Need by", widget: "date" },
      { field: "duration", header: "Duration" }, { field: "priority", header: "Priority" }, { field: "status", header: "Status" },
    ]}
    filterOptions={[{ id: "mine", label: "My records", filter: { owner: { exact: "viewer" } } }]}
    search={{ ...referenceSearch, ...(box ? { box } : {}) }}
  />;
}
