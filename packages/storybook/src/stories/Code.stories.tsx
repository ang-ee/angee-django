import type { Meta, StoryObj } from "@storybook/react-vite";
import { Code, CodeBlock } from "@angee/base";

const meta = {
  title: "Primitives/Code",
  component: Code,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    size: {
      control: "select",
      options: ["sm", "md"],
    },
    box: {
      control: "select",
      options: ["none", "inset", "sheet"],
    },
    tone: {
      control: "select",
      options: ["neutral", "muted", "success", "warning", "danger", "info"],
    },
  },
  args: {
    children: "record.externalId",
    size: "sm",
    box: "inset",
    tone: "neutral",
  },
} satisfies Meta<typeof Code>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const InlineVariants: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-2 text-13">
      <Code box="inset">neutral</Code>
      <Code box="inset" tone="muted">muted</Code>
      <Code box="inset" tone="success">success</Code>
      <Code box="inset" tone="warning">warning</Code>
      <Code box="inset" tone="danger">danger</Code>
      <Code box="inset" tone="info">info</Code>
    </div>
  ),
};

export const Block: Story = {
  render: () => (
    <CodeBlock>{`const status = record.state ?? "draft";\nreturn status.toUpperCase();`}</CodeBlock>
  ),
};
