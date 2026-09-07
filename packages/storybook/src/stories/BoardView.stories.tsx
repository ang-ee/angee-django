import { testResourceQuery, testQueryField, testQueryAxis } from "@angee/metadata/testing";
import { useMemo, useState, type ReactElement } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import type {
  AngeeSchemaMetadata,
  DataResourceFieldMetadata,
  Row,
} from "@angee/metadata";
import { ResourceList, type ListColumn } from "@angee/ui";

import { RuntimeFixture, jsonResponse, storySchema } from "./runtime-fixtures";

interface TaskRow extends Row {
  id: string;
  title: string;
  priority: string;
  stage: { id: string; name: string };
  sort_order: number;
}

const tasks: readonly TaskRow[] = [
  {
    id: "task-research",
    title: "Interview workspace operators",
    priority: "High",
    stage: { id: "stage-backlog", name: "Backlog" },
    sort_order: 1024,
  },
  {
    id: "task-contract",
    title: "Write the ordering contract",
    priority: "Medium",
    stage: { id: "stage-progress", name: "In progress" },
    sort_order: 1024,
  },
  {
    id: "task-errors",
    title: "Exercise exhausted-rank errors",
    priority: "High",
    stage: { id: "stage-progress", name: "In progress" },
    sort_order: 2048,
  },
  {
    id: "task-story",
    title: "Publish interaction stories",
    priority: "Low",
    stage: { id: "stage-progress", name: "In progress" },
    sort_order: 3072,
  },
  {
    id: "task-review",
    title: "Review the static contract",
    priority: "Medium",
    stage: { id: "stage-done", name: "Done" },
    sort_order: 1024,
  },
];

const stages = [
  { id: "stage-backlog", name: "Backlog", position: 1024, fold: true },
  { id: "stage-progress", name: "In progress", position: 2048, fold: false },
  { id: "stage-done", name: "Done", position: 3072, fold: false },
] as const;

const columns = [
  { field: "title", header: "Task" },
  { field: "priority", header: "Priority" },
] satisfies readonly ListColumn<TaskRow>[];

const metadata = {
  angee: {
    resources: [
      {
        query: testResourceQuery({ identity: { field: "id" }, fields: { "id": testQueryField("id", { scalar: "ID", filter: { field: "id", scalar: "ID", values: [], operators: ["exact", "ne", "inList", "notInList", "isNull"] } }),
                "title": testQueryField("title", { scalar: "String", filter: { field: "title", scalar: "String", values: [], operators: ["exact", "ne", "inList", "notInList", "isNull", "contains", "iContains", "startsWith", "iStartsWith", "endsWith", "iEndsWith", "gt", "gte", "lt", "lte"] }, sort: { field: "title" } }),
                "priority": testQueryField("priority", { scalar: "String", filter: null }),
                "stage": testQueryField("stage", { scalar: "ID", kind: "relation", filter: { field: "stage", scalar: "ID", values: [], operators: ["exact", "ne", "inList", "notInList", "isNull"] }, relation: { model: "pm.Stage", identityPath: "stage.id", labelPath: "stage.name" }, row: { path: "stage.id", paths: ["stage.id"] } }),
                "sort_order": testQueryField("sort_order", { scalar: "Float", filter: null, sort: { field: "sort_order" } }) }, axes: { "stage": testQueryAxis("stage", { kind: "relation", identityPath: "stage.id", paths: ["stage.id", "stage.name"], labelPath: "stage.name", server: { input: "stage", key: "stage" }, extractions: [], drill: null }) }, sort: { default: [] } }),

        schemaName: "public",
        modelLabel: "pm.Task",
        appLabel: "pm",
        modelName: "Task",

        roots: {
          list: "tasks",
          detail: "tasks_by_pk",
          create: "insert_tasks_one",
          update: "update_tasks_by_pk",
        },
        typeNames: { node: "TaskType" },
        recordRepresentation: "title",
        capabilities: ["list", "detail", "create", "update"],
        fields: [
          scalarField("id", "ID"),
          scalarField("title", "String", { writable: true }),
          scalarField("priority", "String", { writable: true }),
          relationField("stage", "pm.Stage"),
          scalarField("sort_order", "Float", { writable: true }),
        ],

        aggregateFields: ["id"],

        createFields: ["title", "priority", "stage", "sort_order"],
        updateFields: ["title", "priority", "stage", "sort_order"],
        requiredCreateFields: ["title"],

      },
      {
        query: testResourceQuery({ identity: { field: "id" }, fields: { "id": testQueryField("id", { scalar: "ID", filter: { field: "id", scalar: "ID", values: [], operators: ["exact", "ne", "inList", "notInList", "isNull"] }, sort: { field: "id" } }),
                "name": testQueryField("name", { scalar: "String", filter: { field: "name", scalar: "String", values: [], operators: ["exact", "ne", "inList", "notInList", "isNull", "contains", "iContains", "startsWith", "iStartsWith", "endsWith", "iEndsWith", "gt", "gte", "lt", "lte"] } }),
                "position": testQueryField("position", { scalar: "Float", filter: null, sort: { field: "position" } }),
                "fold": testQueryField("fold", { scalar: "Boolean", filter: null }) }, axes: {}, sort: { default: [{ field: "position", direction: "ASC" }] } }),

        schemaName: "public",
        modelLabel: "pm.Stage",
        appLabel: "pm",
        modelName: "Stage",

        roots: { list: "stages" },
        typeNames: { node: "StageType" },
        recordRepresentation: "name",
        capabilities: ["list"],
        fields: [
          scalarField("id", "ID"),
          scalarField("name", "String"),
          scalarField("position", "Float"),
          scalarField("fold", "Boolean"),
        ],

        aggregateFields: ["id"],

      },
    ],
  },
} satisfies AngeeSchemaMetadata;

const meta = {
  title: "Views/BoardView",
  parameters: { layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const RankedOrdering: Story = {
  render: () => <BoardFixture withFold={false} />,
};

export const FoldDefaultsAndQuickCreate: Story = {
  render: () => <BoardFixture withFold />,
};

function BoardFixture({ withFold }: { withFold: boolean }): ReactElement {
  const [recordId, setRecordId] = useState<string | undefined>(undefined);
  const [creating, setCreating] = useState(false);
  const schemas = useMemo(createBoardStorySchemas, []);
  return (
    <RuntimeFixture schemas={schemas}>
      <div className="min-h-[520px] min-w-[980px]">
        <ResourceList<TaskRow>
          resource="pm.Task"
          columns={columns}
          formFields={[
            { name: "title", label: "Task", title: true },
            { name: "priority", label: "Priority" },
            { name: "stage", label: "Stage", createOnly: true },
            {
              name: "sort_order",
              label: "Order",
              createOnly: true,
            },
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
          laneSource={{
            field: "stage",
            rankField: "sort_order",
            ...(withFold ? { foldField: "fold" } : {}),
          }}
        />
      </div>
    </RuntimeFixture>
  );
}

function createBoardStorySchemas() {
  let storedTasks = tasks.map((task) => ({
    ...task,
    stage: { ...task.stage },
  }));
  let createdCount = 0;
  const schemas = storySchema(async (_input, init) => {
    const request = storyRequest(init);
    const variables = request.variables ?? {};
    const mutationValues = recordValue(variables.object)
      ?? recordValue(variables.values)
      ?? recordValue(variables.input)
      ?? {};
    let mutationTask = storedTasks[0];
    if (request.query.includes("update_tasks_by_pk")) {
      const id = stringValue(variables.id)
        ?? stringValue(recordValue(variables.pk_columns)?.id);
      storedTasks = storedTasks.map((task) => {
        if (task.id !== id) return task;
        mutationTask = taskWithValues(task, mutationValues);
        return mutationTask;
      });
    } else if (request.query.includes("insert_tasks_one")) {
      createdCount += 1;
      mutationTask = taskWithValues(
        {
          id: `task-created-${createdCount}`,
          title: "Untitled task",
          priority: "Medium",
          stage: { id: stages[0].id, name: stages[0].name },
          sort_order: 1024,
        },
        mutationValues,
      );
      storedTasks = [...storedTasks, mutationTask];
    }
    const detailId = stringValue(variables.id);
    const detailTask = storedTasks.find((task) => task.id === detailId)
      ?? mutationTask;
    return jsonResponse({
      data: {
        tasks: {
          totalCount: storedTasks.length,
          results: storedTasks,
          pageInfo: { offset: 0, limit: 50 },
        },
        stages: {
          totalCount: stages.length,
          results: stages,
          pageInfo: { offset: 0, limit: 200 },
        },
        tasks_by_pk: detailTask,
        insert_tasks_one: mutationTask,
        update_tasks_by_pk: mutationTask,
      },
    });
  });
  schemas.public = { ...schemas.public!, metadata };
  return schemas;
}

function taskWithValues(
  task: TaskRow,
  values: Record<string, unknown>,
): TaskRow {
  const stageId = stringValue(values.stage);
  const stage = stageId
    ? stages.find((candidate) => candidate.id === stageId)
    : undefined;
  const rank = values.sort_order;
  return {
    ...task,
    ...(typeof values.title === "string" ? { title: values.title } : {}),
    ...(typeof values.priority === "string"
      ? { priority: values.priority }
      : {}),
    ...(stage ? { stage: { id: stage.id, name: stage.name } } : {}),
    ...(typeof rank === "number" && Number.isFinite(rank)
      ? { sort_order: rank }
      : {}),
  };
}

function storyRequest(init: RequestInit | undefined): {
  query: string;
  variables?: Record<string, unknown>;
} {
  if (typeof init?.body !== "string") return { query: "" };
  try {
    const body = recordValue(JSON.parse(init.body));
    if (!body) return { query: "" };
    const variables = recordValue(body.variables);
    return {
      query: typeof body.query === "string" ? body.query : "",
      ...(variables ? { variables } : {}),
    };
  } catch {
    return { query: "" };
  }
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
  options: { writable?: boolean } = {},
): DataResourceFieldMetadata {
  return {
    name,
    kind: "scalar",
    scalar,
    readable: true,

    aggregatable: name === "id",

    creatable: options.writable === true,
    updatable: options.writable === true,
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

    aggregatable: false,

    creatable: true,
    updatable: true,
    requiredOnCreate: false,
    nullable: false,
  };
}
