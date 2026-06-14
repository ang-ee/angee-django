import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  AppRuntimeProvider,
  GraphQLClientProvider,
  type AngeeUrqlClientOptions,
} from "@angee/sdk";
import {
  ListView,
  ModalsHost,
  baseIcons,
  defaultWidgets,
  type ListColumn,
} from "@angee/base";

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

const storySchemas = {
  public: {
    url: "/graphql/public/",
    fetch: async (input: RequestInfo | URL) => {
      if (String(input).includes("/auth/csrf/")) {
        return jsonResponse({ token: "storybook" });
      }
      return jsonResponse({
        data: {
          notes: {
            totalCount: rows.length,
            results: rows,
            pageInfo: { offset: 0, limit: 50 },
          },
        },
      });
    },
  },
} satisfies Record<string, AngeeUrqlClientOptions>;

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
    <ModalsHost>
      <GraphQLClientProvider config={storySchemas} schema="public">
        <AppRuntimeProvider
          runtime={{
            icons: baseIcons,
            slots: [],
            widgets: defaultWidgets,
          }}
        >
          <div className="max-w-5xl">
            <ListView
              model="notes.Note"
              columns={columns}
              createLabel="New note"
              onCreate={() => undefined}
              pageSize={50}
            />
          </div>
        </AppRuntimeProvider>
      </GraphQLClientProvider>
    </ModalsHost>
  );
}

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    headers: { "content-type": "application/json" },
  });
}
