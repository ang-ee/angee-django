import type { Meta, StoryObj } from "@storybook/react-vite";
import { DirtyPill } from "@angee/ui";

const meta = {
  title: "Fragments/DirtyPill",
  component: DirtyPill,
  parameters: { layout: "centered" },
  argTypes: {
    state: {
      control: "select",
      options: ["dirty", "saving", "saved"],
    },
  },
  args: {
    state: "dirty",
  },
} satisfies Meta<typeof DirtyPill>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const States: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-2">
      <DirtyPill state="dirty" />
      <DirtyPill state="saving" />
      <DirtyPill state="saved" />
    </div>
  ),
};
