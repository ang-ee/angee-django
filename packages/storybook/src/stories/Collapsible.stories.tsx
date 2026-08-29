import type { Meta, StoryObj } from "@storybook/react-vite";
import { Collapsible } from "@angee/ui";

const meta = {
  title: "Primitives/Collapsible",
  component: Collapsible,
  parameters: {
    layout: "padded",
  },
  argTypes: {
    variant: {
      control: "select",
      options: ["default", "row", "section", "flush"],
    },
  },
  args: {
    defaultOpen: true,
    variant: "default",
  },
} satisfies Meta<typeof Collapsible>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {
  render: ({ defaultOpen, variant }) => (
    <Collapsible
      className="mx-auto max-w-xl rounded-6 border border-border-subtle bg-sheet p-4"
      defaultOpen={defaultOpen}
      variant={variant}
    >
      <Collapsible.Trigger>
        <Collapsible.Icon />
        Import notes
      </Collapsible.Trigger>
      <Collapsible.Panel>
        Imports are validated before records are written, and rejected rows stay
        available for review.
      </Collapsible.Panel>
    </Collapsible>
  ),
};

export const SectionHeader: Story = {
  render: () => (
    <Collapsible
      className="mx-auto max-w-xl rounded-6 border border-border-subtle bg-sheet p-3"
      defaultOpen
      variant="section"
    >
      <Collapsible.Trigger>
        <Collapsible.Icon />
        Advanced filters
      </Collapsible.Trigger>
      <Collapsible.Panel>
        <div className="grid gap-2 text-13">
          <span>Status is active</span>
          <span>Owner is assigned</span>
          <span>Updated in the last 7 days</span>
        </div>
      </Collapsible.Panel>
    </Collapsible>
  ),
};
