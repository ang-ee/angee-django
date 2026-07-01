import type { Meta, StoryObj } from "@storybook/react-vite";
import { TextLink } from "@angee/ui";

const meta = {
  title: "Primitives/TextLink",
  component: TextLink,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    variant: {
      control: "select",
      options: ["default", "muted", "block-card"],
    },
  },
  args: {
    children: "Open record",
    href: "#record",
    variant: "default",
  },
} satisfies Meta<typeof TextLink>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Inline: Story = {
  render: () => (
    <p className="max-w-md text-13 text-fg-2">
      Open the <TextLink href="#activity">activity feed</TextLink> to review the
      latest workspace events.
    </p>
  ),
};

export const Variants: Story = {
  render: () => (
    <div className="grid w-80 gap-3">
      <TextLink href="#default">Default link</TextLink>
      <TextLink href="#muted" variant="muted">Muted link</TextLink>
      <TextLink href="#card" variant="block-card">
        <span className="block font-medium">Record detail</span>
        <span className="mt-1 block text-2xs text-fg-muted">Updated 4 minutes ago</span>
      </TextLink>
    </div>
  ),
};
