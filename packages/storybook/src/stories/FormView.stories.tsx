import type { Meta, StoryObj } from "@storybook/react-vite";
import { useMemo } from "react";
import { FORM_CONTAINERS, Field, FormView, GraphView, Group, containersFromChildren, graphNodeStyle, Input, invalidFormSubmit, PrimaryPaneProvider, PrimaryPanePublisher, usePrimaryPaneContent, type FormField, type GroupDescriptor } from "@angee/ui";

import { storySchema, testDataResource, testQueryField, testResourceQuery } from "./runtime-fixtures";

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
  reviewer: { id: "per_grace", name: "Grace Hopper" },
  approver: { id: "per_alan", name: "Alan Turing" },
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
  status: true,
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

// A long form split across several labelled groups, each a titled section of the
// record's sheet (the title/body stay in the header above them).
const sectionGroups = [
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

const scalar = (name: string, type: string, editable = true) => ({
  name, kind: "scalar" as const, scalar: type, readable: true, aggregatable: false,
  creatable: editable, updatable: editable, requiredOnCreate: false,
});
const relation = (name: string, model: string) => ({ ...scalar(name, "ID"), kind: "relation" as const, scalar: null, relationModelLabel: model });
// The file's resources, once: every story's FormView finds notes.Note in the schema's metadata.
const personResource = testDataResource("people.Person", {
  schemaName: "public", recordRepresentation: "name", fields: [scalar("id", "ID", false), scalar("name", "String")],
  // The relation pickers list people too.
  roots: { aggregate: "persons_aggregate" }, typeNames: { filter: "PersonFilter", order: "PersonOrder" },
  query: testResourceQuery({ fields: { id: testQueryField("id", { scalar: "ID" }), name: testQueryField("name", { sort: { field: "name" } }) } }),
});
const noteResource = testDataResource("notes.Note", { schemaName: "public", fields: [
  scalar("id", "ID", false), scalar("title", "String"), scalar("status", "String"), scalar("owner", "String"),
  scalar("priority", "String"), scalar("visibility", "String"), { ...scalar("tags", "String"), kind: "list" as const },
  scalar("createdAt", "DateTime", false), scalar("updatedAt", "DateTime", false), scalar("words", "Int", false),
  scalar("body", "String"), relation("reviewer", personResource.modelLabel), relation("approver", personResource.modelLabel),
] });

// A plan: Title and Summary on the sheet, Risk notes in a collapsed Risks section, Budget and Venue
// each in a pane group, and its steps trailing as editable lines.
const planRecord = {
  id: "plan_launch",
  title: storyRecord.title,
  summary: "Ship the import flow to every workspace.",
  risk: "Imports may time out on the largest workspaces.",
  budget: "12 000 EUR",
  venue: "Room 4",
  steps: [
    { id: "step_freeze", label: "Freeze the release branch", position: 0 },
    { id: "step_smoke", label: "Run the import smoke tests", position: 1 },
  ],
};
const planResource = testDataResource("notes.Plan", {
  schemaName: "public",
  fields: [scalar("id", "ID", false), ...["title", "summary", "risk", "budget", "venue"].map((name) => scalar(name, "String"))],
  linesResource: { field: "steps", modelLabel: "notes.Step", positionField: "position",
    fields: [scalar("label", "String"), scalar("position", "Int")] },
});

const patch = (args: Record<string, unknown>) => isRecord(args._set) ? args._set : isRecord(args.object) ? args.object : {};
const people = [storyRecord.reviewer, storyRecord.approver];
const person = (args: Record<string, unknown>) => {
  const id = isRecord(args.pk_columns) ? args.pk_columns.id : args.id;
  return people.find((candidate) => candidate.id === id) ?? null;
};
const storySchemas = storySchema({
  notes_by_pk: () => storyRecord,
  update_notes_by_pk: (args) => ({ ...storyRecord, ...patch(args) }),
  insert_notes_one: (args) => ({ ...storyRecord, id: "note-new", ...patch(args) }),
  // The relation pickers list people; their Edit reads the person it opens and saves its name.
  persons: () => people,
  persons_aggregate: () => ({ aggregate: { count: people.length } }),
  persons_by_pk: person,
  update_persons_by_pk: (args) => ({ ...person(args), ...patch(args) }),
  // The refused-save stories read their plan; their own submit answers the save, so nothing writes it.
  plans_by_pk: () => planRecord,
}, { resources: [noteResource, personResource, planResource] });

const meta = {
  title: "Views/FormView",
  parameters: { angeeFixture: { schemas: storySchemas }, layout: "padded" },
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

export const GroupedSections: Story = {
  render: () => (
    <FormViewFixture
      fields={editableFields}
      groups={sectionGroups}
    />
  ),
};

function RetainedEditor({ active }: { active: boolean }) {
  const node = useMemo(() => active ? <p>The editor is active.</p> : null, [active]);
  return <><PrimaryPanePublisher node={node} /><Input aria-label="Local editor draft" defaultValue="Retained across tabs" /></>;
}
function PublishedActivity() {
  const { node } = usePrimaryPaneContent();
  return <aside className="rounded-6 border p-3">{node ?? "No editor publication."}</aside>;
}
export const RetainedPanelActivity: Story = {
  render: () => <PrimaryPaneProvider>
    <div className="grid grid-cols-[1fr_16rem] gap-4">
      <FormView resource="notes.Note" id={storyRecord.id} fields={readOnlyFields}
        defaultRecordTab="editor" recordTabs={[{ id: "editor", label: "Editor", keepMounted: true,
          render: ({ active }) => <RetainedEditor active={active} /> }]} />
      <PublishedActivity />
    </div>
  </PrimaryPaneProvider>,
};

const graphNodes = Array.from({ length: 13 }, (_, index) => ({ id: `node-${index}`, kind: "item", title: `Item ${index + 1}` }));
const graphEdges = graphNodes.slice(1).map((node, index) => ({ id: `edge-${index}`, source: graphNodes[index]!.id, target: node.id, kind: "next" }));
export const FullBleedTab: Story = {
  render: () => (
    <div className="h-[600px]">
      <FormView resource="notes.Note" id={storyRecord.id} fields={readOnlyFields} defaultRecordTab="graph"
        recordTabs={[
          { id: "graph", label: "Graph", presentation: "full-bleed", keepMounted: true, render: () =>
            <GraphView className="min-h-0 flex-1" nodes={graphNodes} edges={graphEdges}
              nodeStyles={{ item: graphNodeStyle("var(--border-strong)", "neutral") }} layout={{ rankdir: "LR" }} initialView={{ minZoom: 0.65 }} miniMap /> },
          { id: "activity", label: "Activity", render: () => <p>Document content keeps its centered column.</p> },
        ]} />
    </div>
  ),
};

export const RecordRail: Story = {
  parameters: { angeeFixture: { schemas: storySchemas, runtime: { containers: containersFromChildren(FORM_CONTAINERS, {
    "notes.Note#rail": { "notes.record-properties": {
      content: <FormView.RailGroup id="properties" label="Properties" hint="Edit values here"
        fields={[{ field: { name: "owner" } }, { field: { name: "priority", widget: "select",
          options: [{ value: "HIGH", label: "High" }, { value: "LOW", label: "Low" }] } }]} />,
    } },
  }) } } },
  render: () => (
    <FormView resource="notes.Note" id={storyRecord.id} fields={[titleField, bodyField]}
      acknowledgedSource={{ record: { ...storyRecord, permissions: ["read", "write"] }, values: storyRecord }} />
  ),
};

// Both fields hold a person, whose name a person can edit: the reviewer's picker offers Edit,
// the approver's declines it with `relationEdit: null`.
const reviewerField = { name: "reviewer", label: "Reviewer" } satisfies FormField;
const approverField = { name: "approver", label: "Approver", relationEdit: null } satisfies FormField;
export const RelationWithoutEdit: Story = {
  render: () => (
    <FormView resource="notes.Note" id={storyRecord.id} fields={[titleField, reviewerField, approverField]}
      groups={[{ label: "Review", columns: 2, fields: [reviewerField, approverField], actions: [] }]} />
  ),
};
// A relation field's quick create gets its story here, from the lane that builds it.

/** The plan's fields a refused save can name, each with the reason it is refused for. */
const PLAN_REFUSALS = {
  venue: "Choose a venue that seats the whole team.",
  risk: "Name who owns each risk.",
  budget: "The budget needs a currency.",
  summary: "Say what the launch ships.",
} as const;
type PlanRefusal = keyof typeof PLAN_REFUSALS;

/**
 * The plan opens on its Budget pane; its own submit refuses every save for the field `refused`
 * names, with that field's reason and no other. Edit Summary and press Save: by default Venue,
 * whose pane is not selected, is refused, so the Venue pane is selected and its field takes focus,
 * marked invalid with its reason under it. The banner names the field and does not take focus.
 */
export const RefusedSaveRevealsItsField: StoryObj<{ refused: PlanRefusal }> = {
  args: { refused: "venue" },
  argTypes: { refused: { control: "inline-radio", options: Object.keys(PLAN_REFUSALS) } },
  render: ({ refused }) => (
    <FormView resource={planResource.modelLabel} id={planRecord.id} defaultRecordTab="budget" linesTabLabel="Steps"
      submit={async () => invalidFormSubmit({ fieldErrors: { [refused]: [PLAN_REFUSALS[refused]] }, formErrors: [] })}>
      <Field name="title" label="Title" widget="text" title />
      <Group label="Overview"><Field name="summary" label="Summary" widget="text" /></Group>
      <Group label="Risks" collapsible defaultOpen={false}><Field name="risk" label="Risk notes" widget="text" /></Group>
      <Group label="Budget" pane="budget"><Field name="budget" label="Budget" widget="text" /></Group>
      <Group label="Venue" pane="venue"><Field name="venue" label="Venue" widget="text" /></Group>
    </FormView>
  ),
};

/** The same plan with Risk notes refused: Save opens the collapsed Risks section, Risk notes takes focus, and the Budget pane stays. */
export const RefusedSaveOpensItsSection: StoryObj<{ refused: PlanRefusal }> = {
  ...RefusedSaveRevealsItsField,
  args: { refused: "risk" },
};

export const RecordSectionHeading: Story = {
  render: () => <div className="max-w-3xl">
    <FormView.SectionHeading label="Updates" count="3" summary="2 replies"
      hint="Usually shared with the team" audience="Team members" />
  </div>,
};

function FormViewFixture({
  fields,
  groups,
}: {
  fields: readonly FormField[];
  groups: readonly GroupDescriptor[];
}) {
  return (
    <FormView
      resource="notes.Note"
      id={storyRecord.id}
      fields={fields}
      groups={groups}
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
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
