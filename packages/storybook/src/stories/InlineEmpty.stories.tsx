import type { Meta, StoryObj } from "@storybook/react-vite";
import { InlineEmpty } from "@angee/ui";

const meta = {
  title: "Fragments/InlineEmpty",
  component: InlineEmpty,
  parameters: { layout: "padded" },
} satisfies Meta<typeof InlineEmpty>;

export default meta;

type Story = StoryObj;

export const Compact: Story = {
  render: () => (
    <div className="max-w-xl overflow-hidden rounded-6 border border-border-subtle bg-sheet">
      <InlineEmpty icon="info" label="No activity for this record." />
    </div>
  ),
};
