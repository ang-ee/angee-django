import type { Meta, StoryObj } from "@storybook/react-vite";
import type {
  AngeeSchemaMetadata,
  DataResourceFieldMetadata,
  Row,
} from "@angee/metadata";
import {
  FormView,
  ResourceList,
  type FormField,
  type GroupDescriptor,
  type ListColumn,
} from "@angee/ui";
import { useMemo, useState, type ReactElement, type ReactNode } from "react";

import { RuntimeFixture, jsonResponse, storySchema } from "./runtime-fixtures";

interface TaskRow extends Row {
  id: string;
  title: string;
  project: { id: string; title: string } | null;
  status: string;
  assignee: string | null;
  priority: string;
  due_date: string | null;
  sort_order: number;
  sub_sort_order: number;
}

const project = {
  id: "prj_launch",
  title: "PM suite launch",
  body: "A bounded launch project for the personal planning floor.",
  status: "ACTIVE",
  lead: "usr_alexis",
  start_date: "2026-08-18",
  target_date: "2026-09-04",
  updated_at: "2026-08-22T11:30:00Z",
};

const users = [
  { id: "usr_alexis", username: "alexis" },
  { id: "usr_sofia", username: "sofia" },
  { id: "usr_mina", username: "mina" },
] as const;

const initialTasks: readonly TaskRow[] = [
  {
    id: "tsk_routes",
    title: "Review route ownership",
    project: { id: project.id, title: project.title },
    status: "OPEN",
    assignee: users[0].id,
    priority: "HIGH",
    due_date: "2026-08-24",
    sort_order: 1024,
    sub_sort_order: 1024,
  },
  {
    id: "tsk_needs",
    title: "Exercise capture need",
    project: { id: project.id, title: project.title },
    status: "OPEN",
    assignee: users[0].id,
    priority: "MEDIUM",
    due_date: "2026-08-25",
    sort_order: 2048,
    sub_sort_order: 2048,
  },
  {
    id: "tsk_story",
    title: "Publish project stories",
    project: { id: project.id, title: project.title },
    status: "OPEN",
    assignee: users[1].id,
    priority: "LOW",
    due_date: null,
    sort_order: 3072,
    sub_sort_order: 3072,
  },
  {
    id: "tsk_smoke",
    title: "Run the integration smoke",
    project: { id: project.id, title: project.title },
    status: "OPEN",
    assignee: null,
    priority: "URGENT",
    due_date: "2026-08-27",
    sort_order: 4096,
    sub_sort_order: 4096,
  },
];

const projectFields = [
  { name: "title", label: "Project", title: true },
  {
    name: "status",
    label: "Status",
    widget: "statusbar",
    readOnly: true,
    options: [
      { value: "PLANNED", label: "Planned" },
      { value: "ACTIVE", label: "Active" },
      { value: "ON_HOLD", label: "On hold" },
      { value: "DONE", label: "Done" },
      { value: "CANCELED", label: "Canceled" },
    ],
  },
  { name: "lead", label: "Lead" },
  { name: "start_date", label: "Start date" },
  { name: "target_date", label: "Target date" },
  { name: "updated_at", label: "Updated", readOnly: true },
  { name: "body", label: "Description", widget: "markdown.editor", body: true },
] satisfies readonly FormField[];

const projectGroups = [
  {
    label: "Planning",
    columns: 2,
    fields: projectFields.filter((field) =>
      ["lead", "start_date", "target_date", "updated_at"].includes(field.name),
    ),
    actions: [],
  },
] satisfies readonly GroupDescriptor[];

const taskColumns = [
  { field: "title", header: "Task" },
  { field: "project.title", header: "Project" },
  { field: "priority", header: "Priority" },
  { field: "due_date", header: "Due" },
] satisfies readonly ListColumn<TaskRow>[];

const metadata = {
  angee: {
    resources: [
      {
        schemaName: "public",
        modelLabel: "projects.Project",
        appLabel: "projects",
        modelName: "Project",
        publicIdField: "id",
        roots: {
          list: "projects",
          detail: "projects_by_pk",
          update: "update_projects_by_pk",
        },
        typeNames: { node: "ProjectType" },
        recordRepresentation: "title",
        capabilities: ["list", "detail", "update"],
        fields: [
          scalarField("id", "ID"),
          scalarField("title", "String", true),
          scalarField("body", "String", true),
          enumField("status", ["PLANNED", "ACTIVE", "ON_HOLD", "DONE", "CANCELED"]),
          scalarIdRelationField("lead", "iam.User"),
          scalarField("start_date", "Date", true),
          scalarField("target_date", "Date", true),
          scalarField("updated_at", "DateTime"),
        ],
        filterFields: ["id", "title", "status", "lead"],
        orderFields: ["title", "status", "target_date", "updated_at"],
        aggregateFields: ["id"],
        groupByFields: ["status", "lead"],
        groupDimensions: [
          { field: "status", input: "status", key: "status", kind: "column", scalar: "String" },
          { field: "lead", input: "lead", key: "lead", kind: "relation" },
        ],
        updateFields: ["title", "body", "lead", "start_date", "target_date"],
        relationAxes: [
          {
            field: "lead",
            modelLabel: "iam.User",
            publicIdField: "id",
            labelAxis: "lead__username",
          },
        ],
      },
      {
        schemaName: "public",
        modelLabel: "projects.Task",
        appLabel: "projects",
        modelName: "Task",
        publicIdField: "id",
        roots: {
          list: "project_tasks",
          detail: "project_tasks_by_pk",
          create: "insert_project_tasks_one",
          update: "update_project_tasks_by_pk",
        },
        typeNames: { node: "TaskType" },
        recordRepresentation: "title",
        capabilities: ["list", "detail", "create", "update"],
        fields: [
          scalarField("id", "ID"),
          scalarField("title", "String", true),
          relationField("project", "projects.Project"),
          enumField("status", ["OPEN", "DONE", "DROPPED"]),
          scalarIdRelationField("assignee", "iam.User"),
          enumField("priority", ["NONE", "LOW", "MEDIUM", "HIGH", "URGENT"], true),
          scalarField("due_date", "Date", true),
          scalarField("sort_order", "Float", true),
          scalarField("sub_sort_order", "Float", true),
        ],
        filterFields: ["id", "project", "status", "assignee"],
        orderFields: [
          "project",
          "status",
          "priority",
          "due_date",
          "sort_order",
          "sub_sort_order",
        ],
        aggregateFields: ["id", "sort_order", "sub_sort_order"],
        groupByFields: ["project", "status", "assignee"],
        groupDimensions: [
          { field: "project", input: "project", key: "project", kind: "relation" },
          { field: "status", input: "status", key: "status", kind: "column", scalar: "String" },
          { field: "assignee", input: "assignee", key: "assignee", kind: "relation" },
        ],
        createFields: [
          "title",
          "project",
          "assignee",
          "priority",
          "due_date",
          "sort_order",
          "sub_sort_order",
        ],
        updateFields: [
          "title",
          "project",
          "assignee",
          "priority",
          "due_date",
          "sort_order",
          "sub_sort_order",
        ],
        requiredCreateFields: ["title", "sort_order", "sub_sort_order"],
        relationAxes: [
          {
            field: "project",
            modelLabel: "projects.Project",
            publicIdField: "id",
            labelAxis: "project__title",
          },
          {
            field: "assignee",
            modelLabel: "iam.User",
            publicIdField: "id",
            labelAxis: "assignee__username",
          },
        ],
      },
      {
        schemaName: "public",
        modelLabel: "iam.User",
        appLabel: "iam",
        modelName: "User",
        publicIdField: "id",
        roots: { list: "users" },
        typeNames: { node: "UserType" },
        recordRepresentation: "username",
        capabilities: ["list"],
        fields: [scalarField("id", "ID"), scalarField("username", "String")],
        filterFields: ["id", "username"],
        orderFields: ["username"],
        aggregateFields: ["id"],
        groupByFields: [],
        relationAxes: [],
      },
    ],
  },
} satisfies AngeeSchemaMetadata;

const meta = {
  title: "Addons/Projects",
  parameters: { layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const ProjectRecord: Story = {
  render: () => (
    <ProjectRuntimeFixture>
      <div className="mx-auto max-w-5xl">
        <FormView
          resource="projects.Project"
          id={project.id}
          fields={projectFields}
          groups={projectGroups}
          layout="tabs"
          returning={["body", "status", "lead", "start_date", "target_date", "updated_at"]}
        />
      </div>
    </ProjectRuntimeFixture>
  ),
};

export const TaskBoard: Story = {
  render: () => <TaskBoardFixture />,
};

function TaskBoardFixture(): ReactElement {
  const [recordId, setRecordId] = useState<string | undefined>();
  const [creating, setCreating] = useState(false);
  return (
    <ProjectRuntimeFixture>
      <div className="min-h-[540px] min-w-[1000px]">
        <ResourceList<TaskRow>
          resource="projects.Task"
          columns={taskColumns}
          formFields={[
            { name: "title", label: "Task", title: true },
            { name: "project", label: "Project" },
            { name: "assignee", label: "Assignee" },
            { name: "priority", label: "Priority" },
            { name: "due_date", label: "Due date" },
            { name: "sort_order", label: "Order", createOnly: true },
            { name: "sub_sort_order", label: "Subtask order", createOnly: true },
          ]}
          recordId={recordId}
          creating={creating}
          onSelect={(id) => {
            setCreating(id === null);
            setRecordId(id ?? undefined);
          }}
          onClose={() => {
            setCreating(false);
            setRecordId(undefined);
          }}
          placement="drawer"
          defaultView="board"
          baseFilter={{ status: { exact: "OPEN" } }}
          order={{ sort_order: "ASC" }}
          laneSource={{ field: "assignee", rankField: "sort_order" }}
        />
      </div>
    </ProjectRuntimeFixture>
  );
}

function ProjectRuntimeFixture({ children }: { children: ReactNode }): ReactElement {
  const schemas = useMemo(createProjectSchemas, []);
  return <RuntimeFixture schemas={schemas}>{children}</RuntimeFixture>;
}

function createProjectSchemas() {
  let tasks = initialTasks.map((task) => ({ ...task }));
  const schemas = storySchema(async (_input, init) => {
    const request = requestPayload(init);
    const values = recordValue(request.variables.data)
      ?? recordValue(request.variables.values)
      ?? recordValue(request.variables.input)
      ?? {};
    if (request.query.includes("update_project_tasks_by_pk")) {
      const id = stringValue(request.variables.id)
        ?? stringValue(recordValue(request.variables.pk_columns)?.id);
      tasks = tasks.map((task) => task.id === id ? taskWithValues(task, values) : task);
    }
    const detailId = stringValue(request.variables.id);
    const detailTask = tasks.find((task) => task.id === detailId) ?? tasks[0];
    return jsonResponse({
      data: {
        projects: collection([project]),
        projects_by_pk: project,
        update_projects_by_pk: project,
        project_tasks: collection(tasks),
        project_tasks_by_pk: detailTask,
        insert_project_tasks_one: detailTask,
        update_project_tasks_by_pk: detailTask,
        users: collection(users),
      },
    });
  });
  schemas.public = { ...schemas.public!, metadata };
  return schemas;
}

function taskWithValues(task: TaskRow, values: Record<string, unknown>): TaskRow {
  const assigneeId = stringValue(values.assignee);
  const assignee = assigneeId
    ? assigneeId
    : values.assignee === null
      ? null
      : task.assignee;
  return {
    ...task,
    assignee,
    ...(typeof values.sort_order === "number"
      ? { sort_order: values.sort_order }
      : {}),
    ...(typeof values.sub_sort_order === "number"
      ? { sub_sort_order: values.sub_sort_order }
      : {}),
  };
}

function collection<T>(results: readonly T[]) {
  return {
    totalCount: results.length,
    results,
    pageInfo: { offset: 0, limit: 200 },
  };
}

function requestPayload(init?: RequestInit): {
  query: string;
  variables: Record<string, unknown>;
} {
  if (typeof init?.body !== "string") return { query: "", variables: {} };
  const parsed = recordValue(JSON.parse(init.body));
  return {
    query: typeof parsed?.query === "string" ? parsed.query : "",
    variables: recordValue(parsed?.variables) ?? {},
  };
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : undefined;
}

function scalarField(
  name: string,
  scalar: string,
  writable = false,
): DataResourceFieldMetadata {
  return {
    name,
    kind: "scalar",
    scalar,
    readable: true,
    filterable: true,
    sortable: true,
    aggregatable: name === "id",
    groupable: false,
    creatable: writable,
    updatable: writable,
    requiredOnCreate: false,
    nullable: false,
  };
}

function enumField(
  name: string,
  values: readonly string[],
  writable = false,
): DataResourceFieldMetadata {
  return {
    name,
    kind: "enum",
    values: values.map((value) => ({ value })),
    widget: "select",
    readable: true,
    filterable: true,
    sortable: true,
    aggregatable: false,
    groupable: true,
    creatable: writable,
    updatable: writable,
    requiredOnCreate: false,
    nullable: false,
  };
}

function relationField(
  name: string,
  relationModelLabel: string,
): DataResourceFieldMetadata {
  return {
    name,
    kind: "relation",
    relationModelLabel,
    relationObject: true,
    readable: true,
    filterable: true,
    sortable: false,
    aggregatable: false,
    groupable: true,
    creatable: true,
    updatable: true,
    requiredOnCreate: false,
    nullable: true,
  };
}

function scalarIdRelationField(
  name: string,
  relationModelLabel: string,
): DataResourceFieldMetadata {
  return {
    name,
    kind: "scalar",
    scalar: "ID",
    widget: "select",
    relationModelLabel,
    relationObject: false,
    readable: true,
    filterable: true,
    sortable: false,
    aggregatable: false,
    groupable: true,
    creatable: true,
    updatable: true,
    requiredOnCreate: false,
    nullable: true,
  };
}
