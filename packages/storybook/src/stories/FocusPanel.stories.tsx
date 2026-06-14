import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, FocusPanel, Glyph } from "@angee/base";

const meta = {
  title: "Fragments/FocusPanel",
  component: FocusPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof FocusPanel>;

export default meta;

type Story = StoryObj;

export const Centered: Story = {
  render: () => (
    <div className="mx-auto max-w-2xl">
      <FocusPanel
        actions={
          <Button size="sm" variant="primary">
            <Glyph name="plus" />
            Create source
          </Button>
        }
        eyebrow="Next step"
        subtitle="Connect a resource source before importing records."
        title="Workspace setup"
      >
        <div className="rounded-md bg-inset px-4 py-3 text-13 text-fg-2">
          No sources are configured for this workspace.
        </div>
      </FocusPanel>
    </div>
  ),
};

export const Static: Story = {
  render: () => (
    <div className="mx-auto max-w-2xl">
      <FocusPanel
        collapsible={false}
        subtitle="The panel can render without a disclosure control."
        title="Static focus panel"
      >
        <p className="text-13 text-fg-2">Use this for empty states with a short action path.</p>
      </FocusPanel>
    </div>
  ),
};
