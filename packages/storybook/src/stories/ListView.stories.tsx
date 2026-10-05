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
import { RuntimeFixture, jsonResponse, storySchema } from "./runtime-fixtures";

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

const storySchemas = storySchema(async () =>
  jsonResponse({
    data: {
      notes: {
        totalCount: rows.length,
        results: rows,
        pageInfo: { offset: 0, limit: 50 },
      },
    },
  }),
);

const meta = {
  title: "Views/ListView",
  parameters: { layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const VisibleFieldsChooser: Story = {
  render: () => <ListFixture />,
};

export const SearchShortcuts: Story = {
  render: () => <ListFixture withShortcuts />,
};

function ListFixture({ withShortcuts = false }: { withShortcuts?: boolean }) {
  return (
    <RuntimeFixture schemas={storySchemas}>
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
    </RuntimeFixture>
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
