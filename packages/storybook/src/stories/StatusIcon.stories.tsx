import type { Meta, StoryObj } from "@storybook/react-vite";
import { StatusDot, StatusIcon, TONES } from "@angee/ui";

const meta = {
  title: "Primitives/StatusIcon",
  component: StatusIcon,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    tone: {
      control: "select",
      options: ["info", "success", "warning", "danger", "muted"],
    },
    size: {
      control: "select",
      options: ["sm", "md", "lg"],
    },
  },
  args: {
    tone: "info",
    size: "sm",
  },
} satisfies Meta<typeof StatusIcon>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Icons: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      {(["info", "success", "warning", "danger", "muted"] as const).map((tone) => (
        <StatusIcon key={tone} tone={tone} label={tone} size="md" />
      ))}
    </div>
  ),
};

export const Dots: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      {TONES.map((tone) => (
        <StatusDot key={tone} tone={tone} label={tone} />
      ))}
    </div>
  ),
};
