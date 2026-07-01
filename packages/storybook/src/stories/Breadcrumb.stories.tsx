import type { Meta, StoryObj } from "@storybook/react-vite";
import { Breadcrumb } from "@angee/ui";

const meta = {
  title: "Chrome/Breadcrumb",
  component: Breadcrumb,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof Breadcrumb>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Trail: Story = {
  render: () => (
    <div className="w-[42rem] overflow-hidden rounded-8 border border-border-subtle">
      <Breadcrumb />
    </div>
  ),
};
