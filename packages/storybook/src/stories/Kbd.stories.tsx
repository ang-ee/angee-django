import type { Meta, StoryObj } from "@storybook/react-vite";
import { Kbd } from "@angee/ui";

const meta = {
  title: "Primitives/Kbd",
  component: Kbd,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    size: {
      control: "select",
      options: ["sm", "md", "lg"],
    },
    tone: {
      control: "select",
      options: ["default", "subtle", "rail"],
    },
  },
  args: {
    children: "⌘K",
    size: "md",
    tone: "default",
  },
} satisfies Meta<typeof Kbd>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Kbd size="sm">Esc</Kbd>
      <Kbd size="md">⌘K</Kbd>
      <Kbd size="lg">Enter</Kbd>
    </div>
  ),
};

export const Tones: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Kbd tone="default">⌘K</Kbd>
      <Kbd tone="subtle">Shift</Kbd>
      <div className="rounded-6 bg-rail p-3">
        <Kbd tone="rail">/</Kbd>
      </div>
    </div>
  ),
};
