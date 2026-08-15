import type { Meta, StoryObj } from "@storybook/react-vite";
import { TopMenuTabs } from "@angee/ui";

import { topMenuTabs } from "./chrome-fixtures";

const meta = {
  title: "Chrome/TopMenuTabs",
  component: TopMenuTabs,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof TopMenuTabs>;

export default meta;

type Story = StoryObj<typeof meta>;

export const DataTabs: Story = {
  args: { tabs: topMenuTabs },
  render: (args) => (
    <div className="rounded-6 bg-rail p-2 text-on-rail">
      <TopMenuTabs {...args} />
    </div>
  ),
};
