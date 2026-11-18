import type { Meta, StoryObj } from "@storybook/react-vite";
import { Card, CardContent, DateTile, textRoleVariants } from "@angee/ui";

const meta = {
  title: "Fragments/DateTile",
  component: DateTile,
  parameters: { layout: "padded" },
  args: { value: "2026-09-28T19:00:00Z", timeZone: "UTC" },
} satisfies Meta<typeof DateTile>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Sizes: Story = {
  render: (args) => (
    <div className="flex items-end gap-3 bg-inset p-4">
      <DateTile {...args} size="sm" />
      <DateTile {...args} size="md" />
      <DateTile {...args} size="lg" />
    </div>
  ),
};

export const Pending: Story = {
  args: { value: null },
  render: (args) => (
    <div className="flex items-end gap-3 bg-inset p-4">
      <DateTile {...args} size="sm" />
      <DateTile {...args} size="md" />
      <DateTile {...args} size="lg" pendingLabel="To be scheduled" />
    </div>
  ),
};

/** Over a card's media strip, the way a listing shows when each item happens. */
export const OnACard: Story = {
  render: (args) => (
    <Card className="w-72 overflow-hidden p-0">
      <div className="relative h-24 bg-brand-soft">
        <DateTile {...args} className="absolute left-2.5 top-2.5 shadow-sm" />
      </div>
      <CardContent className="grid gap-1 p-3">
        <p className={textRoleVariants({ role: "title" })}>Evening reading circle</p>
        <p className={textRoleVariants({ role: "meta" })}>North studio · 40 seats</p>
      </CardContent>
    </Card>
  ),
};
