import type { Meta, StoryObj } from "@storybook/react-vite";
import { Badge, Button, Glyph, SurfacePanel } from "@angee/ui";

const meta = {
  title: "Fragments/SurfacePanel",
  component: SurfacePanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SurfacePanel>;

export default meta;

type Story = StoryObj;

export const Panel: Story = {
  render: () => (
    <SurfacePanel
      actions={
        <Button size="sm" variant="secondary">
          <Glyph name="plus" />
          Add
        </Button>
      }
      summary="4 pending"
      title="Release queue"
    >
      <div className="divide-y divide-border-subtle">
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <span>Base fragments</span>
          <Badge tone="success">Ready</Badge>
        </div>
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <span>Console panels</span>
          <Badge tone="info">Draft</Badge>
        </div>
      </div>
    </SurfacePanel>
  ),
};
