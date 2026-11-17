import type { ReactElement } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  GalleryView,
  RESOURCE_CONTAINERS,
  ResourceList,
  containersFromChildren,
  type ResourceCardContent,
  type ResourceListProps,
} from "@angee/ui";

import { columns, createTaskStorySchemas, taskCard, type TaskRow } from "./task-collection-fixture";

const meta = {
  title: "Views/GalleryView",
  parameters: { angeeFixture: () => ({ schemas: createTaskStorySchemas() }), layout: "padded", owner: GalleryView },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

/** The gallery kind of a model-backed list with no card declared: each record is
 * the list's column card (the first column as its title, then the details) in an
 * inset frame, with paging, search and selection from the shared list. */
export const ColumnCard: Story = {
  render: () => <TaskGallery />,
};

/** One `renderCard` for both kinds: switch between Grid view and Board view and
 * the same flush card draws each record, with selection and links kept. */
export const OneCardOnBothSurfaces: Story = {
  render: () => <TaskGallery renderCard={taskCard} cardFrame="flush" />,
};

/** The task card as an addon registers it: content typed for any row of its model. */
const taskModelCard: ResourceCardContent = {
  render: (row, context) => taskCard(row as TaskRow, context),
  frame: "flush",
};

/** The model's card registered once, as an addon does in its manifest
 * (`containers: { "pm.Task#card": { … } }`): the page declares no card, and the
 * gallery and board kinds both draw it, in the frame it names. */
export const RegisteredModelCard: Story = {
  parameters: {
    angeeFixture: () => ({
      schemas: createTaskStorySchemas(),
      runtime: {
        containers: containersFromChildren(RESOURCE_CONTAINERS, {
          "pm.Task#card": {
            "demo.task": { content: taskModelCard, requiredFields: ["stage"] },
          },
        }),
      },
    }),
  },
  render: () => <TaskGallery />,
};

function TaskGallery({ renderCard, cardFrame }: Pick<ResourceListProps<TaskRow>, "renderCard" | "cardFrame">): ReactElement {
  return (
    <div className="min-h-[520px] min-w-[980px]">
      <ResourceList<TaskRow>
        resource="pm.Task"
        columns={columns}
        fields={["stage"]}
        scope="local"
        hideCreate
        defaultView="gallery"
        laneSource={{ field: "stage", rankField: "sort_order" }}
        renderCard={renderCard}
        cardFrame={cardFrame}
      />
    </div>
  );
}
