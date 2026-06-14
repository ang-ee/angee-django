import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, CollectionHeader, Glyph } from "@angee/base";

const meta = {
  title: "Fragments/CollectionHeader",
  component: CollectionHeader,
  parameters: { layout: "padded" },
} satisfies Meta<typeof CollectionHeader>;

export default meta;

type Story = StoryObj;

export const Collection: Story = {
  render: () => (
    <div className="max-w-4xl overflow-hidden rounded-md border border-border-subtle">
      <CollectionHeader
        count="245"
        description="Operational notes, release briefs, and system references."
        icon="list"
        title="Notes"
        actions={
          <Button size="sm" variant="primary">
            <Glyph name="plus" />
            New note
          </Button>
        }
      />
    </div>
  ),
};
