import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  ListView,
  PaneToolbarHost,
  RowsListView,
  type ListSearchDeclaration,
  defineRowAction,
  type ListColumn,
  type RowActionDeclaration,
} from "@angee/ui";

import { ResourceQuery } from "@angee/metadata";
import { resourceSnapshotOperations, storySchema, testDataResource, testQueryField, testResourceQuery } from "./runtime-fixtures";

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

// Forty chores, more than the table library samples to choose a sort, ordered by effort, most first: an order that does
// not lead with their status. A quarter of them have no status. By first appearance their groups are todo, done, none,
// doing; by the sum of their efforts, most first, done, none, doing, todo.
const choreStatuses = ["todo", "done", null, "doing"] as const;
const chores = Array.from({ length: 40 }, (_, index) => ({
  id: `chore-${index + 1}`, title: `Chore ${String(index + 1).padStart(2, "0")}`, status: choreStatuses[index % 4], effort: (index * 7) % 40,
}));
const choreFields = { id: { scalar: "ID" }, title: { scalar: "String" }, status: { scalar: "String" }, effort: { scalar: "Int" } } as const;
const choreQuery = ResourceQuery.forRows({ fields: choreFields }).contract;
const choreResource = testDataResource("notes.Chore", {
  schemaName: "public", rowModel: "client", recordRepresentation: "title",
  roots: { list: "chores", aggregate: "chores_aggregate" }, typeNames: { filter: "ChoreFilter", order: "ChoreOrder" },
  fields: Object.entries(choreFields).map(([name, { scalar }]) => ({ name, kind: "scalar", scalar, readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
  query: { ...choreQuery, sort: { ...choreQuery.sort, default: [{ field: "effort", direction: "DESC" as const }] } },
});

// The same tasks held on a server, two more of them with no status, grouped by its groups read: the resource
// snapshot answers that read in the order it asks for, as the server does.
const serverTasks = [
  ...tasks, { id: "task-7", title: "Sort the inbox", status: null, rank: 2 }, { id: "task-8", title: "File the receipts", status: null, rank: 1 },
];
const serverTaskQuery = ResourceQuery.forRows({ fields: taskFields }).contract;
serverTaskQuery.axes.status!.server = { input: "STATUS", key: "status" };
serverTaskQuery.axes.status!.drill = { kind: "value", field: "status", valueKey: "status", nullMode: "isNull", valueMap: [] };
const serverTaskResource = testDataResource("notes.ServerTask", {
  schemaName: "public", recordRepresentation: "title",
  roots: { list: "server_tasks", aggregate: "server_tasks_aggregate", groups: "server_tasks_groups", groupsCount: "server_tasks_groups_count" },
  typeNames: { filter: "ServerTaskFilter", order: "ServerTaskOrder" },
  fields: Object.entries(taskFields).map(([name, { scalar }]) => ({ name, kind: "scalar", scalar, readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false })),
  query: { ...serverTaskQuery, sort: { ...serverTaskQuery.sort, default: (["status", "rank", "title"] as const).map((field) => ({ field, direction: "ASC" as const })) } },
});
/** The server tasks' groups read, as a generated groups document names it: the groups, and their exact count. */
const SERVER_TASK_GROUPS = `query ServerTaskGroups($group_by: [ServerTaskGroupBySpec!]!, $where: ServerTaskFilter, $having: ServerTaskHaving,
  $order_by: [ServerTaskGroupOrder!], $limit: Int, $offset: Int) {
  server_tasks_groups(group_by: $group_by, where: $where, having: $having, order_by: $order_by, limit: $limit, offset: $offset) {
    key { status } aggregate { count }
  }
  totalCount: server_tasks_groups_count(group_by: $group_by, where: $where, having: $having)
}`;

const storySchemas = storySchema({
  notes: () => rows,
  notes_aggregate: () => ({ aggregate: { count: rows.length } }),
  tasks: () => tasks,
  tasks_aggregate: () => ({ aggregate: { count: tasks.length } }),
  chores: () => chores,
  chores_aggregate: () => ({ aggregate: { count: chores.length } }),
  ...resourceSnapshotOperations([serverTaskResource], { "notes.ServerTask": serverTasks }),
}, { resources: [testDataResource("notes.Note", {
  schemaName: "public",
  roots: { list: "notes", aggregate: "notes_aggregate" },
  typeNames: { filter: "NoteFilter", order: "NoteOrder" },
  query: testResourceQuery({ fields: Object.fromEntries(([
    ["id", "ID"], ["title", "String"], ["tags", "String"], ["status", "String"],
    ["owner", "String"], ["words", "Int"], ["updatedAt", "DateTime"],
  ] as const).map(([name, scalar]) => [name, testQueryField(name, { scalar, sort: { field: name } })])) }),
}), taskResource, choreResource, serverTaskResource] });

const meta = {
  title: "Views/ListView",
  parameters: {
    angeeFixture: { schemas: storySchemas, operationDocuments: { public: { groups: { "notes.ServerTask": SERVER_TASK_GROUPS } } } },
    layout: "padded",
  },
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
 * rows. At load the Status header shows ascending (the direction its groups go in: a group's tasks
 * share their status), Rank shows ascending (each group's rows follow it) and Title shows not sorted
 * (it only breaks rank ties: Book the room before Draft the brief). Only Rank's header carries an
 * `aria-sort` that says a direction: the Status header's sort button and arrow name its groups'
 * direction, and `aria-sort`, one header at a time, stays on the key the rows follow. Press Title's
 * sort button: Title sorts ascending with the status key kept behind it, which a press never drops,
 * Rank shows not sorted, and each group's rows follow their titles (Collect quotes moves above Draft
 * the brief) while the groups keep their status order. Press Status's: the groups turn, open before
 * closed, and their rows keep their titles' order, which Title's header still shows: a press on the
 * grouping column leaves the records' order alone. Pressed at load, it turns the groups the same and
 * Rank keeps its ascending sort and its `aria-sort`, Title still breaking the rank ties.
 */
export const GroupedWithTieBreakers: Story = {
  name: "Grouped With Tie-breakers",
  render: () => (
    <div className="max-w-3xl">
      <GroupedTasks />
    </div>
  ),
};

/**
 * The same tasks as a side pane's list, grouped by status and ordered by status, then rank, then
 * title. The pane toolbar's More menu holds an Order group that checks "Rank, ascending" and offers
 * Rank and Title, each ascending and descending, and not Status, whose header turns the groups.
 * Choose "Title, descending": the groups stay where they are, the Status header still shows
 * ascending and the Title header shows descending. Choosing the item already checked changes nothing.
 */
export const PaneWithOrder: Story = {
  render: () => (
    <div className="h-[420px] w-[28rem] border border-border-subtle bg-canvas">
      <PaneToolbarHost>
        <GroupedTasks pane />
      </PaneToolbarHost>
    </div>
  ),
};

/**
 * Forty chores grouped by status in the browser and ordered by effort, most first. The groups list by
 * their status, doing, done, todo, and the chores without one last, under "No value"; the Status
 * header shows ascending (the direction its groups go in) and Effort descending. Open a group: its
 * chores follow their effort. Press Status's sort button: the groups turn, todo, done, doing, the
 * group without a status still last, and Effort still shows descending: each group's chores keep
 * their effort order. Press Title's: each group's chores follow their titles and the groups stay
 * turned. Press Status's again: the groups turn back, the chores still by their titles.
 */
export const GroupedBehindAnotherKey: Story = {
  name: "Grouped Behind Another Key",
  render: () => (
    <div className="max-w-3xl">
      <ListView resource="notes.Chore" defaultGroup={{ field: "status" }} columns={[
        { field: "status", header: "Status" }, { field: "effort", header: "Effort", align: "right" }, { field: "title", header: "Title" },
      ]} />
    </div>
  ),
};

/**
 * The tasks grouped by status on the server, two of them with no status, in the order their resource declares
 * (status, rank, then title). The groups read asks for the groups ascending: closed, open, then the tasks without a
 * status, under "No value"; the Status header shows ascending. Press Status's sort button: the read asks for them
 * descending, open before closed, the group without a status still last, and the header shows descending, while Rank
 * still shows ascending: the read asks for each group's tasks by rank, then title, behind the status key. Press it
 * again to turn them back. The snapshot lists each group's tasks in its own order, whatever the records' order asks.
 */
export const GroupedOnTheServer: Story = {
  name: "Grouped On The Server",
  render: () => (
    <div className="max-w-3xl">
      <ListView resource="notes.ServerTask" defaultGroup={{ field: "status" }} columns={[
        { field: "status", header: "Status" }, { field: "rank", header: "Rank", align: "right" }, { field: "title", header: "Title" },
      ]} />
    </div>
  ),
};

/** The tasks grouped by status, in the order their resource declares; `pane` lists them as a side pane does. */
function GroupedTasks({ pane = false }: { pane?: boolean }) {
  return (
    <ListView resource="notes.Task" defaultGroup={{ field: "status" }} {...(pane ? { pane: {} } : {})} columns={[
      { field: "status", header: "Status" }, { field: "rank", header: "Rank", align: "right" }, { field: "title", header: "Title" },
    ]} />
  );
}

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
