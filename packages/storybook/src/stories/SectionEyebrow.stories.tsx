import type { Meta, StoryObj } from "@storybook/react-vite";
import { SectionEyebrow } from "@angee/ui";

const meta = {
  title: "Primitives/SectionEyebrow",
  component: SectionEyebrow,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    size: {
      control: "select",
      options: ["xs", "sm"],
    },
    tone: {
      control: "select",
      options: ["muted", "fg", "brand", "danger"],
    },
    tracking: {
      control: "select",
      options: ["normal", "wide", "wider"],
    },
    weight: {
      control: "select",
      options: ["medium", "semibold"],
    },
  },
  args: {
    children: "Workspace",
    size: "xs",
    tone: "muted",
  },
} satisfies Meta<typeof SectionEyebrow>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Tones: Story = {
  render: () => (
    <div className="grid gap-3">
      <SectionEyebrow>Muted</SectionEyebrow>
      <SectionEyebrow tone="fg">Foreground</SectionEyebrow>
      <SectionEyebrow tone="brand">Brand</SectionEyebrow>
      <SectionEyebrow tone="danger">Danger</SectionEyebrow>
    </div>
  ),
};

export const Spacing: Story = {
  render: () => (
    <div className="w-56 rounded-6 border border-border-subtle bg-sheet p-2">
      <SectionEyebrow spacing="menu">Menu</SectionEyebrow>
      <div className="rounded-6 px-2 py-1.5 text-13 text-fg">Overview</div>
      <div className="rounded-6 px-2 py-1.5 text-13 text-fg-muted">Activity</div>
    </div>
  ),
};
