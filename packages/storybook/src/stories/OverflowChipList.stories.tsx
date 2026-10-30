import type { Meta, StoryObj } from "@storybook/react-vite";
import { OverflowChipList } from "@angee/ui";

/** A bounded chip wrap: the first `max` items, then one "+N" chip naming the rest. */
const meta = {
  title: "Fragments/OverflowChipList",
  component: OverflowChipList,
  parameters: { layout: "centered" },
} satisfies Meta<typeof OverflowChipList>;

export default meta;
type Story = StoryObj<typeof meta>;

const items = ["iam", "parties", "messaging", "storage", "knowledge", "workflows", "decisions", "projects"].map((name, index) => {
  const id = `demo_${index + 1}`;
  return { id, label: name, href: `/records/${id}` };
});

export const Default: Story = { args: { items, max: 6 } };
export const Unlinked: Story = { args: { items: items.map(({ id, label }) => ({ id, label })), max: 4, tone: "neutral" } };
