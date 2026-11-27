import { useMemo, type ReactElement } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { ResourceList, type FormField, type RecordTabDescriptor, type ResourceRecordPlacement } from "@angee/ui";

import { RoutedRuntimeFixture, storyAppearance, type RuntimeFixtureProps } from "./runtime-fixtures";
import { columns, createTaskStorySchemas, type TaskRow } from "./task-collection-fixture";

/**
 * A routed collection in the console shell with one record open, beside its list or in its
 * place. The record, in the document presentation, has one pane that makes it taller than the
 * room it opens in, so it scrolls there: in the content pane of the list's own workbench (inside
 * the console's) when split, in the list's record sheet when inline. A record in the workspace
 * presentation, or with a full-bleed pane active, scrolls inside the form in either placement.
 */
const meta = {
  title: "Views/ResourceList",
  component: ResourceList,
  parameters: { angeeOwnRuntime: true, layout: "fullscreen" },
} satisfies Meta<typeof ResourceList>;

export default meta;

type Story = StoryObj;

/** The list beside the open record; the record scrolls in the content pane of the list's own workbench. */
export const Split: Story = {
  render: (_args, { globals }) => <TaskCollection placement="split" appearance={storyAppearance(globals)} />,
};

/**
 * The same record in place of the list, the list's default placement; it scrolls in the list's
 * record sheet. The catalog links a component's story named `Default`, else its lowest id, which
 * is this one today.
 */
export const Inline: Story = {
  render: (_args, { globals }) => <TaskCollection placement="inline" appearance={storyAppearance(globals)} />,
};

const formFields = [
  { name: "title", label: "Task", title: true },
  { name: "priority", label: "Priority" },
  { name: "stage", label: "Stage" },
] satisfies readonly FormField[];

const checkpoints = Array.from({ length: 40 }, (_, index) => `Checkpoint ${index + 1}: confirm the step with its owner and note what changed.`);
const recordTabs: readonly RecordTabDescriptor[] = [{
  id: "checkpoints",
  label: "Checkpoints",
  render: () => <div className="grid gap-3">{checkpoints.map((line) => <p key={line}>{line}</p>)}</div>,
}];

function TaskCollection({ placement, appearance }: {
  placement: ResourceRecordPlacement;
  appearance: RuntimeFixtureProps["appearance"];
}): ReactElement {
  const schemas = useMemo(() => createTaskStorySchemas(), []);
  return <RoutedRuntimeFixture appearance={appearance} schemas={schemas} collectionPath="/tasks" initialEntry="/tasks/task-contract"
    resourceName="pm.Task" resourceLabel="Tasks">
    <ResourceList<TaskRow> resource="pm.Task" columns={columns} formFields={formFields} recordTabs={recordTabs}
      placement={placement} routed />
  </RoutedRuntimeFixture>;
}
