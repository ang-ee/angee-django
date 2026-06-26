import type { Meta, StoryObj } from "@storybook/react-vite";
import { ConsoleLayout } from "@angee/ui";

import { LayoutStoryBody } from "./chrome-fixtures";

const meta = {
  title: "Layout/ConsoleLayout",
  component: ConsoleLayout,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof ConsoleLayout>;

export default meta;

type Story = StoryObj;

export const Default: Story = {
  render: () => (
    <div className="-m-6 h-screen w-screen">
      <ConsoleLayout>
        <LayoutStoryBody />
      </ConsoleLayout>
    </div>
  ),
};
