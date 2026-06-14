import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, InfoRow } from "@angee/base";

const meta = {
  title: "Fragments/InfoRow",
  component: InfoRow,
  parameters: { layout: "padded" },
} satisfies Meta<typeof InfoRow>;

export default meta;

type Story = StoryObj;

export const Row: Story = {
  render: () => (
    <dl className="max-w-xl overflow-hidden rounded-md border border-border-subtle bg-sheet py-2">
      <InfoRow
        label="Slug"
        value="q3-review-brief"
        action={
          <Button size="sm" variant="link">
            Copy
          </Button>
        }
      />
      <InfoRow label="Owner" value="Sofia Marin" />
      <InfoRow label="Archived" />
    </dl>
  ),
};
