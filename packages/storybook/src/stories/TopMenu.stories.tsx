import type { Meta, StoryObj } from "@storybook/react-vite";
import { TopMenu } from "@angee/ui";

import { chromeMenuItems, topMenuTabs } from "./chrome-fixtures";

const meta = {
  title: "Chrome/TopMenu",
  component: TopMenu,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof TopMenu>;

export default meta;

type Story = StoryObj<typeof meta>;

export const RuntimeMenu: Story = {
  render: () => (
    <div className="rounded-6 bg-rail p-2 text-on-rail">
      <TopMenu items={chromeMenuItems} />
    </div>
  ),
};

export const DataTabs: Story = {
  render: () => (
    <div className="rounded-6 bg-rail p-2 text-on-rail">
      <TopMenu tabs={topMenuTabs} />
    </div>
  ),
};
