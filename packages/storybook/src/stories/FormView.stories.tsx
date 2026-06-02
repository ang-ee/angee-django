import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  AppRuntimeProvider,
  GraphQLClientProvider,
  type AngeeUrqlClientOptions,
} from "@angee/sdk";
import {
  FormView,
  ModalsHost,
  defaultWidgets,
  type FormField,
  type PageGroupDescriptor,
} from "@angee/base";

const statusOptions = [
  { value: "DRAFT", label: "Draft" },
  { value: "ACTIVE", label: "Active" },
  { value: "ARCHIVED", label: "Archived" },
];

const storyRecord = {
  id: "note-1",
  title: "Launch checklist",
  status: "ACTIVE",
  summary: "Coordinate the final launch review and open follow ups.",
  owner: "Ada Lovelace",
  updatedAt: "2026-06-01T15:30:00Z",
  body:
    "Confirm release notes, review support handoff, and verify the import smoke test.",
};

const titleField = {
  name: "title",
  label: "Title",
  widget: "text",
  title: true,
  placeholder: storyRecord.title,
} satisfies FormField;
const statusField = {
  name: "status",
  label: "Status",
  widget: "statusbar",
  options: statusOptions,
} satisfies FormField;
const summaryField = {
  name: "summary",
  label: "Summary",
  widget: "textarea",
} satisfies FormField;
const ownerField = {
  name: "owner",
  label: "Owner",
  widget: "text",
  readOnly: true,
} satisfies FormField;
const updatedAtField = {
  name: "updatedAt",
  label: "Updated At",
  widget: "datetime",
  readOnly: true,
} satisfies FormField;
const bodyField = {
  name: "body",
  label: "Body",
  widget: "textarea",
} satisfies FormField;

const editableFields = [
  titleField,
  statusField,
  summaryField,
  ownerField,
  updatedAtField,
  bodyField,
] satisfies readonly FormField[];

const editableGroups = [
  {
    label: "Details",
    columns: 2,
    fields: [summaryField, ownerField, updatedAtField],
    actions: [],
  },
  {
    label: "Body",
    fields: [bodyField],
    actions: [],
  },
] satisfies readonly PageGroupDescriptor[];

const readOnlyTitleField = {
  ...titleField,
  readOnly: true,
} satisfies FormField;
const readOnlyStatusField = {
  ...statusField,
  readOnly: true,
} satisfies FormField;
const readOnlySummaryField = {
  ...summaryField,
  readOnly: true,
} satisfies FormField;
const readOnlyOwnerField = {
  ...ownerField,
  readOnly: true,
} satisfies FormField;
const readOnlyUpdatedAtField = {
  ...updatedAtField,
  readOnly: true,
} satisfies FormField;
const readOnlyBodyField = {
  ...bodyField,
  readOnly: true,
} satisfies FormField;

const readOnlyFields = [
  readOnlyTitleField,
  readOnlyStatusField,
  readOnlySummaryField,
  readOnlyOwnerField,
  readOnlyUpdatedAtField,
  readOnlyBodyField,
] satisfies readonly FormField[];

const readOnlyGroups = [
  {
    label: "Details",
    columns: 2,
    fields: [readOnlySummaryField, readOnlyOwnerField, readOnlyUpdatedAtField],
    actions: [],
  },
  {
    label: "Body",
    fields: [readOnlyBodyField],
    actions: [],
  },
] satisfies readonly PageGroupDescriptor[];

const storySchemas = {
  public: {
    url: "/graphql/public/",
    fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/auth/csrf/")) {
        return jsonResponse({ token: "storybook" });
      }
      const payload = requestPayload(init);
      const patch = isRecord(payload.variables.data)
        ? payload.variables.data
        : {};

      if (payload.query.includes("mutation updateNote")) {
        return jsonResponse({ data: { updateNote: { ...storyRecord, ...patch } } });
      }
      if (payload.query.includes("mutation createNote")) {
        return jsonResponse({
          data: { createNote: { ...storyRecord, id: "note-new", ...patch } },
        });
      }
      return jsonResponse({ data: { note: storyRecord } });
    },
  },
} satisfies Record<string, AngeeUrqlClientOptions>;

const meta = {
  title: "Views/FormView",
  parameters: { layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const EditMode: Story = {
  render: () => (
    <FormViewFixture fields={editableFields} groups={editableGroups} />
  ),
};

export const ReadOnlyMode: Story = {
  render: () => (
    <FormViewFixture fields={readOnlyFields} groups={readOnlyGroups} />
  ),
};

function FormViewFixture({
  fields,
  groups,
}: {
  fields: readonly FormField[];
  groups: readonly PageGroupDescriptor[];
}) {
  return (
    <ModalsHost>
      <GraphQLClientProvider config={storySchemas} schema="public">
        <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
          <div className="mx-auto max-w-5xl rounded-md border border-border bg-sheet p-6">
            <FormView
              model="notes.Note"
              id={storyRecord.id}
              fields={fields}
              groups={groups}
              returning={["summary", "owner", "updatedAt", "body"]}
            />
          </div>
        </AppRuntimeProvider>
      </GraphQLClientProvider>
    </ModalsHost>
  );
}

function requestPayload(init?: RequestInit): {
  query: string;
  variables: Record<string, unknown>;
} {
  if (typeof init?.body !== "string") return { query: "", variables: {} };
  const parsed: unknown = JSON.parse(init.body);
  if (!isRecord(parsed)) return { query: "", variables: {} };
  return {
    query: typeof parsed.query === "string" ? parsed.query : "",
    variables: isRecord(parsed.variables) ? parsed.variables : {},
  };
}

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    headers: { "content-type": "application/json" },
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
