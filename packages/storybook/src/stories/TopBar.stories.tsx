import type { Meta, StoryObj } from "@storybook/react-vite";
import { ChatterProvider, TopBar } from "@angee/ui";

import { chromeMenuItems } from "./chrome-fixtures";

const meta = {
  title: "Chrome/TopBar",
  component: TopBar,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof TopBar>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ConsoleChrome: Story = {
  render: () => (
    <ChatterProvider>
      <div className="w-[58rem] overflow-hidden rounded-8 border border-border-on-rail">
        <TopBar
          menuItems={chromeMenuItems}
          showChatterToggle
          showUserMenu
        />
      </div>
    </ChatterProvider>
  ),
};
