import type { Meta, StoryObj } from "@storybook/react-vite";
import { Badge, CountBadge, FILLS, TONES } from "@angee/base";

const meta = {
  title: "Primitives/Badge",
  component: Badge,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    tone: {
      control: "select",
      options: [...TONES],
    },
    variant: {
      control: "select",
      options: [...FILLS],
    },
    shape: {
      control: "inline-radio",
      options: ["rounded", "pill"],
    },
    density: {
      control: "select",
      options: ["default", "compact", "micro", "tiny"],
    },
  },
  args: {
    children: "Published",
    tone: "success",
    variant: "soft",
    shape: "rounded",
    density: "default",
  },
} satisfies Meta<typeof Badge>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Tones: Story = {
  render: () => (
    <div className="flex max-w-xl flex-wrap gap-2">
      {TONES.map((tone) => (
        <Badge key={tone} tone={tone}>
          {tone}
        </Badge>
      ))}
    </div>
  ),
};

export const Fills: Story = {
  render: () => (
    <div className="flex flex-col gap-2">
      {(["brand", "success", "danger"] as const).map((tone) => (
        <div key={tone} className="flex items-center gap-2">
          {FILLS.map((variant) => (
            <Badge key={variant} tone={tone} variant={variant}>
              {variant}
            </Badge>
          ))}
        </div>
      ))}
    </div>
  ),
};

export const Shapes: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Badge tone="brand" shape="rounded">
        rounded
      </Badge>
      <Badge tone="brand" shape="pill">
        pill
      </Badge>
    </div>
  ),
};

export const Counts: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <CountBadge value={3} />
      <CountBadge tone="brand" value={12} />
      <CountBadge tone="danger" value={128} max={99} />
      <CountBadge tone="success" size="md" value={7} />
    </div>
  ),
};
