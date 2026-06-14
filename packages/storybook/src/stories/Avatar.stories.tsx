import type { Meta, StoryObj } from "@storybook/react-vite";
import { Avatar, AvatarStack, Glyph } from "@angee/base";

const meta = {
  title: "Primitives/Avatar",
  component: Avatar,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    size: {
      control: "select",
      options: ["sm", "md", "lg", "xl", "xxl"],
    },
  },
  args: {
    initials: "SM",
    size: "md",
  },
} satisfies Meta<typeof Avatar>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Avatar initials="SM" size="sm" />
      <Avatar initials="SM" size="md" />
      <Avatar initials="SM" size="lg" />
      <Avatar initials="SM" size="xl" />
      <Avatar initials="SM" size="xxl" />
    </div>
  ),
};

export const ColorSlots: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      {["SM", "AK", "JR", "TL", "MN", "BC", "DP", "EG"].map((initials) => (
        <Avatar key={initials} initials={initials} />
      ))}
    </div>
  ),
};

export const WithGlyph: Story = {
  render: () => (
    <Avatar size="lg" aria-label="Workspace">
      <Glyph name="layout-dashboard" size={16} />
    </Avatar>
  ),
};

export const Stack: Story = {
  render: () => (
    <AvatarStack>
      <Avatar initials="SM" />
      <Avatar initials="AK" />
      <Avatar initials="JR" />
      <Avatar initials="+3" />
    </AvatarStack>
  ),
};
