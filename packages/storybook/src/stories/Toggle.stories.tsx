import type { Meta, StoryObj } from "@storybook/react-vite";
import { Glyph, Toggle } from "@angee/base";

const meta = {
  title: "Primitives/Toggle",
  component: Toggle,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    size: {
      control: "select",
      options: ["sm", "md", "icon", "icon-sm"],
    },
    variant: {
      control: "select",
      options: ["default", "ghost", "outline"],
    },
  },
  args: {
    children: "Pinned",
    size: "md",
    variant: "default",
  },
} satisfies Meta<typeof Toggle>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Variants: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Toggle defaultPressed>Default</Toggle>
      <Toggle variant="ghost" defaultPressed>
        Ghost
      </Toggle>
      <Toggle variant="outline" defaultPressed>
        Outline
      </Toggle>
      <Toggle size="iconMd" aria-label="Star">
        <Glyph name="star" size={16} />
      </Toggle>
    </div>
  ),
};
