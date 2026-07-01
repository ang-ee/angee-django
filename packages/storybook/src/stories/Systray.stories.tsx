import type { Meta, StoryObj } from "@storybook/react-vite";
import { Systray } from "@angee/ui";

const meta = {
  title: "Chrome/Systray",
  component: Systray,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof Systray>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <div className="rounded-6 bg-rail p-3 text-on-rail">
      <Systray />
    </div>
  ),
};
