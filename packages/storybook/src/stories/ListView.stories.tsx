import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  ListView,
  defineRowAction,
  type ListColumn,
  type RowActionDeclaration,
} from "@angee/ui";

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

export const QuickFilterRow: Story = {
  render: () => <ListFixture withFilterRow />,
};

function ListFixture({ withFilterRow = false }: { withFilterRow?: boolean }) {
  return (
    <RuntimeFixture schemas={storySchemas}>
      <div className="max-w-5xl">
        <ListView
          resource="notes.Note"
          columns={columns}
          rowActions={rowActions}
          chrome={{ heading: { label: "Notes", hint: "Shared team records", audience: "Editors" } }}
          filterRow={withFilterRow ? { quickFilterIds: ["status:ACTIVE"], facetIds: ["status"] } : undefined}
          filterOptions={withFilterRow ? [
            { id: "status:ACTIVE", label: "Active", filter: { status: { exact: "ACTIVE" } } },
            { id: "status:DRAFT", label: "Draft", filter: { status: { exact: "DRAFT" } } },
            { id: "status:ARCHIVED", label: "Archived", filter: { status: { exact: "ARCHIVED" } } },
          ] : undefined}
          customFilterFields={withFilterRow ? [{ id: "status", label: "Status", type: "selection", options: [
            { value: "ACTIVE", label: "Active" }, { value: "DRAFT", label: "Draft" }, { value: "ARCHIVED", label: "Archived" },
          ] }] : undefined}
          createLabel="New note"
          onCreate={() => undefined}
        />
      </div>
    </RuntimeFixture>
  );
}
