import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph, MiniCard, RailPanel, Chip } from "@angee/ui";

const meta = {
  title: "Fragments/RailPanel",
  component: RailPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof RailPanel>;

export default meta;

type Story = StoryObj;

export const Section: Story = {
  render: () => (
    <div className="max-w-xs">
      <RailPanel
        actions={
          <Button aria-label="Add source" size="iconSm" variant="ghost">
            <Glyph name="plus" />
          </Button>
        }
        count="3"
        title="Active sources"
      >
        <div className="grid gap-2">
          <MiniCard
            meta="Synced 4 minutes ago"
            primaryTag={{ label: "Live", tone: "success" }}
            title="Production API"
          />
          <MiniCard
            meta="Manual import"
            tags={<Chip tone="info">CSV</Chip>}
            title="Partner export"
          />
        </div>
      </RailPanel>
    </div>
  ),
};

export const Empty: Story = {
  render: () => (
    <div className="max-w-xs">
      <RailPanel empty="No sources match the active filter." title="Active sources" />
    </div>
  ),
};

