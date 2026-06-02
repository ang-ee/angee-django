import type { Meta, StoryObj } from "@storybook/react-vite";
import { UserMenu } from "@angee/base";

const meta = {
  title: "Chrome/UserMenu",
  component: UserMenu,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof UserMenu>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <div className="rounded-md bg-rail p-3 text-on-rail">
      <UserMenu />
    </div>
  ),
};
