import type { Meta, StoryObj } from "@storybook/react-vite";
import { AppBrand } from "@angee/ui";

const meta = {
  title: "Chrome/AppBrand",
  component: AppBrand,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof AppBrand>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <div className="rounded-6 bg-rail p-2 text-on-rail">
      <AppBrand />
    </div>
  ),
};
