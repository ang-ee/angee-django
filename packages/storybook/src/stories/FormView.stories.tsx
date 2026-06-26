import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  FormView,
  type FormField,
  type GroupDescriptor,
} from "@angee/ui";

import { RuntimeFixture, jsonResponse, storySchema } from "./runtime-fixtures";

const statusOptions = [
  { value: "DRAFT", label: "Draft" },
  { value: "ACTIVE", label: "Active" },
  { value: "ARCHIVED", label: "Archived" },
];

const storyRecord = {
  id: "note_7u9e3b1f",
  title: "Launch readiness review",
  status: "ACTIVE",
  owner: "Ada Lovelace",
  priority: "HIGH",
  visibility: "TEAM",
  tags: ["release", "support", "imports"],
  createdAt: "2026-05-28T10:00:00Z",
  updatedAt: "2026-06-01T15:30:00Z",
  words: 842,
  body:
    "## Review goals\n\n- Confirm release notes and owner handoff.\n- Verify import smoke tests against the seeded workspace.\n- Close support-facing follow ups before the launch window.\n\n## Open items\n\nThe final permissions review is scheduled after the data import check.",
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
const ownerField = {
  name: "owner",
  label: "Owner",
  widget: "text",
  readOnly: true,
} satisfies FormField;
const priorityField = {
  name: "priority",
  label: "Priority",
  widget: "select",
  options: [
    { value: "LOW", label: "Low" },
    { value: "MEDIUM", label: "Medium" },
    { value: "HIGH", label: "High" },
  ],
} satisfies FormField;
const visibilityField = {
  name: "visibility",
  label: "Visibility",
  widget: "select",
  options: [
    { value: "PRIVATE", label: "Private" },
    { value: "TEAM", label: "Team" },
    { value: "PUBLIC", label: "Public" },
  ],
} satisfies FormField;
const tagsField = {
  name: "tags",
  label: "Tags",
  widget: "tagInput",
} satisfies FormField;
const createdAtField = {
  name: "createdAt",
  label: "Created At",
  widget: "datetime",
  readOnly: true,
} satisfies FormField;
const updatedAtField = {
  name: "updatedAt",
  label: "Updated At",
  widget: "datetime",
  readOnly: true,
} satisfies FormField;
const wordsField = {
  name: "words",
  label: "Words",
  widget: "integer",
  readOnly: true,
} satisfies FormField;
const bodyField = {
  name: "body",
  label: "Description",
  widget: "markdown.editor",
  body: true,
} satisfies FormField;

const editableFields = [
  titleField,
  statusField,
  ownerField,
  priorityField,
  visibilityField,
  tagsField,
  createdAtField,
  updatedAtField,
  wordsField,
  bodyField,
] satisfies readonly FormField[];

const editableGroups = [
  {
    label: "Details",
    columns: 2,
    fields: [
      ownerField,
      priorityField,
      visibilityField,
      createdAtField,
      updatedAtField,
      wordsField,
      tagsField,
    ],
    actions: [],
  },
] satisfies readonly GroupDescriptor[];

// A long form split across several labelled groups — rendered as tabs via
// `layout="tabs"` (the title/body stay in the header above the tab strip).
const tabbedGroups = [
  {
    label: "Ownership",
    columns: 2,
    fields: [ownerField, priorityField, visibilityField],
    actions: [],
  },
  {
    label: "Timestamps",
    columns: 2,
    fields: [createdAtField, updatedAtField, wordsField],
    actions: [],
  },
  {
    label: "Tags",
    columns: 1,
    fields: [tagsField],
    actions: [],
  },
] satisfies readonly GroupDescriptor[];

const readOnlyFields = editableFields.map((field) => ({
  ...field,
  readOnly: true,
})) satisfies readonly FormField[];

const readOnlyFieldByName = new Map(
  readOnlyFields.map((field) => [field.name, field]),
);

const readOnlyGroups = editableGroups.map((group) => ({
  ...group,
  fields: group.fields.map(readOnlyFieldFor),
})) satisfies readonly GroupDescriptor[];

function readOnlyFieldFor(field: FormField): FormField {
  const readOnlyField = readOnlyFieldByName.get(field.name);
  if (!readOnlyField) {
    throw new Error(`No read-only field derived for ${field.name}`);
  }
  return readOnlyField;
}

const storySchemas = storySchema(async (_input, init) => {
  const payload = requestPayload(init);
  const patch = isRecord(payload.variables.data) ? payload.variables.data : {};

  if (payload.query.includes("mutation updateNote")) {
    return jsonResponse({ data: { updateNote: { ...storyRecord, ...patch } } });
  }
  if (payload.query.includes("mutation createNote")) {
    return jsonResponse({
      data: { createNote: { ...storyRecord, id: "note-new", ...patch } },
    });
  }
  return jsonResponse({ data: { note: storyRecord } });
});

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

export const TabbedLayout: Story = {
  render: () => (
    <FormViewFixture
      fields={editableFields}
      groups={tabbedGroups}
      layout="tabs"
    />
  ),
};

function FormViewFixture({
  fields,
  groups,
  layout,
}: {
  fields: readonly FormField[];
  groups: readonly GroupDescriptor[];
  layout?: "stacked" | "tabs";
}) {
  return (
    <RuntimeFixture schemas={storySchemas}>
      <FormView
        resource="notes.Note"
        id={storyRecord.id}
        fields={fields}
        groups={groups}
        layout={layout}
        returning={[
          "owner",
          "priority",
          "visibility",
          "tags",
          "createdAt",
          "updatedAt",
          "words",
          "body",
        ]}
      />
    </RuntimeFixture>
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
