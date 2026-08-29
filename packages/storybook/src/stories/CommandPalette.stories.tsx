import type { Meta, StoryObj } from "@storybook/react-vite";
import { CommandPalette } from "@angee/ui";

const meta = {
  title: "Chrome/CommandPalette",
  component: CommandPalette,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof CommandPalette>;

export default meta;

type Story = StoryObj<typeof meta>;

// The rail-styled trigger; click it (or press ⌘K) to open the palette. The
// global preview decorator supplies the menu — its destinations become the nav
// commands — and the router that each command navigates through.
export const Trigger: Story = {
  render: () => (
    <div className="rounded-6 bg-rail p-3 text-on-rail">
      <CommandPalette />
    </div>
  ),
};
