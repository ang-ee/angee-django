import type { Meta, StoryObj } from "@storybook/react-vite";
import { NavLink } from "@angee/ui";

const meta = {
  title: "Primitives/NavLink",
  component: NavLink,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    variant: {
      control: "select",
      options: ["unstyled", "inline", "block"],
    },
  },
  args: {
    active: false,
    children: "Overview",
    href: "#overview",
    variant: "inline",
  },
} satisfies Meta<typeof NavLink>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Inline: Story = {
  render: () => (
    <nav className="flex items-center gap-4 text-13">
      <NavLink href="#overview" variant="inline" active>Overview</NavLink>
      <NavLink href="#activity" variant="inline">Activity</NavLink>
      <NavLink href="#files" variant="inline">Files</NavLink>
    </nav>
  ),
};

export const Block: Story = {
  render: () => (
    <nav className="w-56 rounded-6 border border-border-subtle bg-sheet p-1 text-13">
      <NavLink
        href="#overview"
        variant="block"
        active
        className="rounded-6 bg-brand-soft px-2 py-1.5 text-brand-soft-text"
      >
        Overview
      </NavLink>
      <NavLink
        href="#activity"
        variant="block"
        className="rounded-6 px-2 py-1.5 text-fg-muted hover:bg-inset hover:text-fg"
      >
        Activity
      </NavLink>
      <NavLink
        href="#files"
        variant="block"
        disabled
        className="rounded-6 px-2 py-1.5 text-fg-muted"
      >
        Files
      </NavLink>
    </nav>
  ),
};
