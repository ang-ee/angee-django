import type { Meta, StoryObj } from "@storybook/react-vite";
import { ConsoleShell } from "@angee/base";

import { ShellStoryBody } from "./chrome-fixtures";

const meta = {
  title: "Shell/ConsoleShell",
  component: ConsoleShell,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof ConsoleShell>;

export default meta;

type Story = StoryObj;

export const Default: Story = {
  render: () => (
    <div className="-m-6 h-screen w-screen">
      <ConsoleShell
        title="Notes"
        icon="notes"
        breadcrumbs={[
          { label: "Console", to: "/notes" },
          { label: "Notes" },
        ]}
      >
        <ShellStoryBody />
      </ConsoleShell>
    </div>
  ),
};
