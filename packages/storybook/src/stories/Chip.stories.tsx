import type { Meta, StoryObj } from "@storybook/react-vite";
import { Chip, FILLS, TONES } from "@angee/base";

const meta = {
  title: "Primitives/Chip",
  component: Chip,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    tone: {
      control: "select",
      // Chip adds two local tones outside the palette: `muted` and `inherit`.
      options: [...TONES, "muted", "inherit"],
    },
    variant: {
      control: "select",
      options: [...FILLS],
    },
    size: {
      control: "inline-radio",
      options: ["micro", "sm", "md"],
    },
    shape: {
      control: "inline-radio",
      options: ["rounded", "pill"],
    },
    mono: {
      control: "boolean",
    },
  },
  args: {
    children: "in-review",
    tone: "info",
    variant: "soft",
    size: "sm",
    shape: "pill",
    mono: false,
  },
} satisfies Meta<typeof Chip>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Tones: Story = {
  render: () => (
    <div className="flex max-w-xl flex-wrap items-center gap-2">
      {TONES.map((tone) => (
        <Chip key={tone} tone={tone}>
          {tone}
        </Chip>
      ))}
    </div>
  ),
};

export const Fills: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      {FILLS.map((variant) => (
        <Chip key={variant} tone="brand" variant={variant}>
          {variant}
        </Chip>
      ))}
    </div>
  ),
};

export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Chip tone="info" size="micro">
        micro
      </Chip>
      <Chip tone="info" size="sm">
        sm
      </Chip>
      <Chip tone="info" size="md">
        md
      </Chip>
    </div>
  ),
};

export const Specials: Story = {
  name: "muted & inherit",
  render: () => (
    <div className="flex items-center gap-2">
      <Chip tone="muted">muted</Chip>
      <Chip tone="muted" mono>
        token
      </Chip>
      <span className="inline-flex items-center gap-2 text-brand">
        <Chip tone="inherit">inherit</Chip>
        adopts the surrounding text color
      </span>
    </div>
  ),
};
