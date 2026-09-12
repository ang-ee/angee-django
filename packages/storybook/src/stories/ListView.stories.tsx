import { testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { AngeeSchemaMetadata } from "@angee/metadata";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, userEvent, waitFor, within } from "storybook/test";
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
    owner: "Alexis",
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
    tone: {
      ACTIVE: "success",
      DRAFT: "warning",
      ARCHIVED: "neutral",
    },
  },
  { field: "owner", header: "Owner" },
  { field: "words", header: "Words", align: "right" },
  { field: "updatedAt", header: "Updated" },
] satisfies readonly ListColumn<StoryRow>[];

const rowActions: readonly RowActionDeclaration<StoryRow>[] = [
  defineRowAction({
    kind: "page",
    id: "open-note",
    label: "Open note",
    icon: "pencil",
    variant: "ghost",
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

function ListFixture() {
  return (
    <RuntimeFixture schemas={storySchemas}>
      <div className="max-w-5xl">
        <ListView
          resource="notes.Note"
          columns={columns}
          rowActions={rowActions}
          createLabel="New note"
          onCreate={() => undefined}
        />
      </div>
    </RuntimeFixture>
  );
}

const people = [
  { id: "person-alexis", name: "Alexis" },
  { id: "person-sofia", name: "Sofia" },
  { id: "person-mina", name: "Mina" },
  { id: "person-grace", name: "Grace" },
];

const bulkRows = rows.map(({ id, title, owner }) => ({
  id,
  title,
  owner: people.find((person) => person.name === owner) ?? null,
}));

type BulkRow = (typeof bulkRows)[number];

const bulkColumns = [
  { field: "title", header: "Title" },
  { field: "owner", header: "Owner" },
] satisfies readonly ListColumn<BulkRow>[];

const bulkNoteMetadata = {
  angee: {
    resources: [
      {
        query: testResourceQuery({
          identity: { field: "id" },
          fields: {
            id: testQueryField("id", { scalar: "ID", kind: "scalar", filter: null }),
            title: testQueryField("title", { scalar: "String", kind: "scalar", filter: null, sort: { field: "title" } }),
            owner: testQueryField("owner", {
              scalar: "ID",
              kind: "relation",
              filter: null,
              relation: { model: "notes.Person", identityPath: "owner.id", labelPath: "owner.name" },
              row: { path: "owner.id", paths: ["owner.id"] },
            }),
          },
          axes: {},
          sort: { default: [] },
        }),
        schemaName: "public",
        modelLabel: "notes.Note",
        appLabel: "notes",
        modelName: "Note",
        roots: { list: "notes", aggregate: "notes_aggregate", update: "update_notes_by_pk" },
        typeNames: { node: "NoteType", filter: "NoteBoolExp", order: "NoteOrderBy" },
        recordRepresentation: "title",
        capabilities: ["list", "aggregate", "update"],
        fields: [
          {
            name: "id",
            kind: "scalar",
            scalar: "ID",
            readable: true,
            aggregatable: false,
            creatable: false,
            updatable: false,
            requiredOnCreate: false,
          },
          {
            name: "title",
            kind: "scalar",
            scalar: "String",
            readable: true,
            aggregatable: false,
            creatable: true,
            updatable: true,
            requiredOnCreate: true,
          },
          {
            name: "owner",
            kind: "relation",
            relationModelLabel: "notes.Person",
            relationObject: true,
            readable: true,
            aggregatable: false,
            creatable: true,
            updatable: true,
            requiredOnCreate: false,
            nullable: true,
          },
        ],
        aggregateFields: [],
      },
      {
        query: testResourceQuery({
          identity: { field: "id" },
          fields: {
            id: testQueryField("id", { scalar: "ID", kind: "scalar", filter: null }),
            name: testQueryField("name", { scalar: "String", kind: "scalar", filter: null, sort: { field: "name" } }),
          },
          axes: {},
          sort: { default: [] },
        }),
        schemaName: "public",
        modelLabel: "notes.Person",
        appLabel: "notes",
        modelName: "Person",
        roots: { list: "people" },
        typeNames: { node: "PersonType" },
        recordRepresentation: "name",
        capabilities: ["list"],
        fields: [
          {
            name: "id",
            kind: "scalar",
            scalar: "ID",
            readable: true,
            aggregatable: false,
            creatable: false,
            updatable: false,
            requiredOnCreate: false,
          },
          {
            name: "name",
            kind: "scalar",
            scalar: "String",
            readable: true,
            aggregatable: false,
            creatable: true,
            updatable: true,
            requiredOnCreate: true,
          },
        ],
        aggregateFields: [],
      },
    ],
  },
} satisfies AngeeSchemaMetadata;

const bulkEditSchemas = storySchema(async () =>
  jsonResponse({
    data: {
      notes: bulkRows,
      notes_aggregate: { aggregate: { count: bulkRows.length } },
      people,
      people_aggregate: { aggregate: { count: people.length } },
      update_notes_by_pk: bulkRows[0],
    },
  }),
);
bulkEditSchemas.public = { ...bulkEditSchemas.public!, metadata: bulkNoteMetadata };

/** Two rows selected, the Edit menu listing the updatable columns, and Set Owner's relation picker open. */
export const BulkEditSelection: Story = {
  render: () => <BulkEditFixture />,
  play: async ({ canvasElement }) => {
    // The list loads and the menu and dialog fade in; wait for each before acting on it.
    const patience = { timeout: 5000 };
    const canvas = within(canvasElement);
    const [first, second] = await canvas.findAllByRole("checkbox", { name: "Select row" }, patience);
    await userEvent.click(first!);
    await userEvent.click(second!);
    await userEvent.click(await canvas.findByRole("button", { name: "Edit" }, patience));
    const page = within(canvasElement.ownerDocument.body);
    await expect(await page.findByRole("menuitem", { name: "Set Title" }, patience)).toBeInTheDocument();
    const setOwner = await page.findByRole("menuitem", { name: "Set Owner" }, patience);
    await waitFor(() => expect(setOwner).toBeVisible(), patience);
    await userEvent.click(setOwner);
    const dialog = await page.findByRole("dialog", undefined, patience);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Owner" })).toBeVisible(), patience);
  },
};

function BulkEditFixture() {
  return (
    <RuntimeFixture schemas={bulkEditSchemas}>
      <div className="max-w-5xl">
        <ListView resource="notes.Note" columns={bulkColumns} />
      </div>
    </RuntimeFixture>
  );
}
