import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph, SurfaceHeader } from "@angee/ui";

const meta = {
  title: "Fragments/SurfaceHeader",
  component: SurfaceHeader,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SurfaceHeader>;

export default meta;

type Story = StoryObj;

export const Surface: Story = {
  render: () => (
    <div className="max-w-4xl overflow-hidden rounded-6 border border-border-subtle">
      <SurfaceHeader
        fetching
        icon="layout-dashboard"
        subtitle="Workspace activity, resource health, and active operator context."
        title="Workspace overview"
      >
        <Button size="sm" variant="secondary">
          <Glyph name="activity" />
          Refresh
        </Button>
      </SurfaceHeader>
    </div>
  ),
};
