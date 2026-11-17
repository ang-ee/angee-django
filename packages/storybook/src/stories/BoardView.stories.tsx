import { useState, type ReactElement } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { ResourceList } from "@angee/ui";

import { columns, createTaskStorySchemas, taskCard, type TaskRow } from "./task-collection-fixture";

const meta = {
  title: "Views/BoardView",
  parameters: { angeeFixture: () => ({ schemas: createTaskStorySchemas() }), layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const RankedOrdering: Story = {
  render: () => <BoardFixture withFold={false} />,
};

export const FoldDefaultsAndQuickCreate: Story = {
  render: () => <BoardFixture withFold />,
};

export const DeclaredCard: Story = {
  render: () => <BoardFixture withFold={false} withCard />,
};

/** A graphical card drawn by `renderCard` in the flush frame: its art band reaches
 * the card's edges, the drag handle and checkbox overlay it on hover, and the same
 * renderer draws the gallery kind (Views/GalleryView). */
export const CustomCardFlush: Story = {
  render: () => <BoardFixture withFold={false} customCard />,
};

function BoardFixture({ withFold, withCard = false, customCard = false }: {
  withFold: boolean; withCard?: boolean; customCard?: boolean;
}): ReactElement {
  const [recordId, setRecordId] = useState<string | undefined>(undefined);
  const [creating, setCreating] = useState(false);
  return (
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
        boardCard={withCard ? { title: "title", fields: ["priority"] } : undefined}
        renderCard={customCard ? taskCard : undefined}
        cardFrame={customCard ? "flush" : undefined}
        laneSource={{
          field: "stage",
          rankField: "sort_order",
          ...(withFold ? { foldField: "fold" } : {}),
        }}
      />
    </div>
  );
}
