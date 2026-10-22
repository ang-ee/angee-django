import type { Meta, StoryObj } from "@storybook/react-vite";
import { StatusDot, StatusIcon, StatusIconGroup, TONES } from "@angee/ui";

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

export const CheckGroup: Story = {
  parameters: { owner: StatusIconGroup },
  render: () => <StatusIconGroup label="Record checks" items={[
    { key: "identity", label: "Identity: matched", tone: "success" },
    { key: "duplicate", label: "Duplicate: none found", tone: "success" },
    { key: "source", label: "Source: review needed", tone: "warning", description: "The source differs from the previous submission." },
    { key: "contents", label: "Contents: conflict", tone: "danger", description: "The submitted value disagrees with the reference." },
    { key: "reference", label: "Reference: unresolved", tone: "muted", description: "The supporting document has not arrived." },
  ]} />,
};
