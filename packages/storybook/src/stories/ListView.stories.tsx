import type { Meta, StoryObj } from "@storybook/react-vite";
import { ListView, type ListColumn } from "@angee/ui";

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
          createLabel="New note"
          onCreate={() => undefined}
        />
      </div>
    </RuntimeFixture>
  );
}
