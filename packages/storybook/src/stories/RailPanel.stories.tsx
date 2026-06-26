import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph, MiniCard, RailPanel, Tag } from "@angee/ui";

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
          <Button aria-label="Add source" size="iconSm" variant="icon">
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
            tags={<Tag tone="info">CSV</Tag>}
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

