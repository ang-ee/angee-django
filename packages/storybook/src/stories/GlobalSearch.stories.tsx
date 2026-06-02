import type { Meta, StoryObj } from "@storybook/react-vite";
import { GlobalSearch } from "@angee/base";

const meta = {
  title: "Chrome/GlobalSearch",
  component: GlobalSearch,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof GlobalSearch>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Pill: Story = {
  render: () => (
    <div className="rounded-md bg-rail p-3 text-on-rail">
      <GlobalSearch />
    </div>
  ),
};
