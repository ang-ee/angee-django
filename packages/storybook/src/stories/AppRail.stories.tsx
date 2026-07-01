import type { Meta, StoryObj } from "@storybook/react-vite";
import { AppRail } from "@angee/ui";

const meta = {
  title: "Chrome/AppRail",
  component: AppRail,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof AppRail>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <div className="h-[34rem] overflow-hidden rounded-8 border border-border-subtle bg-canvas">
      <AppRail />
    </div>
  ),
};
