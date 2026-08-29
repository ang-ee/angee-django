import type { Meta, StoryObj } from "@storybook/react-vite";
import { AppChooser } from "@angee/ui";

import { appChooserItems } from "./chrome-fixtures";

const meta = {
  title: "Chrome/AppChooser",
  component: AppChooser,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof AppChooser>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Open: Story = {
  render: () => (
    <div className="h-[32rem] w-[48rem] rounded-8 border border-border-subtle bg-rail p-3 text-on-rail">
      <AppChooser
        items={appChooserItems}
        activeId="notes"
        defaultOpen
        side="bottom"
        align="start"
      />
    </div>
  ),
};
