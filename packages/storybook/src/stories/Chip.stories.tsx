import type { Meta, StoryObj } from "@storybook/react-vite";
import { Chip, CountBadge, FILLS, TONES } from "@angee/ui";

/**
 * The one pill: tone × fill × size × shape, a status `mark`, and a `pressed`
 * toggle mode (scope chips, reactions). `CountBadge` is the numeric count pill.
 */
const meta = {
  title: "Primitives/Chip",
  component: Chip,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    tone: {
      control: "select",
      options: [...TONES, "inherit"],
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
    mark: {
      control: "inline-radio",
      options: ["none", "dot", "icon"],
    },
    mono: {
      control: "boolean",
    },
  },
  args: {
    children: "in-review",
    tone: "info",
    variant: "soft",
    size: "micro",
    shape: "pill",
    mark: "none",
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
    <div className="flex flex-col gap-2">
      {(["brand", "success", "danger", "muted"] as const).map((tone) => (
        <div key={tone} className="flex items-center gap-2">
          {FILLS.map((variant) => (
            <Chip key={variant} tone={tone} variant={variant}>
              {variant}
            </Chip>
          ))}
        </div>
      ))}
    </div>
  ),
};

export const SizesAndShapes: Story = {
  name: "Sizes and shapes",
  render: () => (
    <div className="flex flex-col gap-2">
      {(["pill", "rounded"] as const).map((shape) => (
        <div key={shape} className="flex items-center gap-2">
          {(["micro", "sm", "md"] as const).map((size) => (
            <Chip key={size} tone="info" shape={shape} size={size}>
              {shape} · {size}
            </Chip>
          ))}
        </div>
      ))}
    </div>
  ),
};

export const Marks: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Chip tone="success" mark="icon">Published</Chip>
      <Chip tone="warning" mark="dot" variant="ghost">Running</Chip>
      <Chip tone="danger" variant="ghost">Failed</Chip>
    </div>
  ),
};

export const Pressed: Story = {
  name: "Pressed (toggle mode)",
  render: () => (
    <div className="flex items-center gap-2">
      <Chip size="sm" tone="muted" pressed>All · 12</Chip>
      <Chip size="sm" tone="muted" pressed={false}>Needs you · 1</Chip>
      <Chip size="md" tone="muted" variant="surface" pressed>👍 3</Chip>
      <Chip tone="inherit" size="md" onClick={() => undefined}>Show quoted</Chip>
    </div>
  ),
};

export const Specials: Story = {
  name: "mono, inherit & record colour",
  render: () => (
    <div className="flex items-center gap-2">
      <Chip tone="muted" mono>
        token
      </Chip>
      <span className="inline-flex items-center gap-2 text-brand">
        <Chip tone="inherit">inherit</Chip>
        adopts the surrounding text color
      </span>
      <Chip color="#fa8072">salmon</Chip>
    </div>
  ),
};

export const Counts: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <CountBadge value={3} />
      <CountBadge tone="brand" value={12} />
      <CountBadge tone="danger" value={128} max={99} />
      <CountBadge tone="success" size="md" value={7} />
      <CountBadge tone="muted" variant="ghost" value={1024} />
      <span className="relative inline-grid size-8 place-content-center rounded-6 bg-inset">
        ⌘
        <CountBadge placement="overlay" size="xs" tone="warning" variant="solid" value={2} />
      </span>
    </div>
  ),
};
