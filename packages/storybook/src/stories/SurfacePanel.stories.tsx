import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph, ListItem, SurfacePanel } from "@angee/ui";

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
        <ListItem
          meta="Framework build"
          status={{ label: "Ready", tone: "success" }}
          title="Base fragments"
        />
        <ListItem
          meta="Storybook review"
          status={{ label: "Draft", tone: "info" }}
          title="Console panels"
        />
      </div>
    </SurfacePanel>
  ),
};
