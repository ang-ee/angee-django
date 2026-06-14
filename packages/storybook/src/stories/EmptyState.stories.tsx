import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, EmptyState, Glyph } from "@angee/base";

const meta = {
  title: "Fragments/EmptyState",
  component: EmptyState,
  parameters: { layout: "padded" },
} satisfies Meta<typeof EmptyState>;

export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => (
    <div className="max-w-3xl">
      <EmptyState
        icon="archive"
        title="No records"
        description="Create the first record or adjust the active filters."
        actions={
          <>
            <Button size="sm" variant="secondary">
              Clear filters
            </Button>
            <Button size="sm" variant="primary">
              <Glyph name="plus" />
              New record
            </Button>
          </>
        }
      />
    </div>
  ),
};

export const Fill: Story = {
  name: "Fill (centered in parent)",
  render: () => (
    // `fill` grows the panel to fill — and center within — its parent, replacing
    // the `grid place-content-center` wrapper hosts used to hand-roll.
    <div className="grid h-96 rounded-md border border-border-subtle">
      <EmptyState
        fill
        icon="note"
        title="Select a page"
        description="Choose a page from the navigator to start reading."
      />
    </div>
  ),
};
