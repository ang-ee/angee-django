import type { Meta, StoryObj } from "@storybook/react-vite";
import { RadioGroup } from "@angee/ui";

const meta = {
  title: "Primitives/RadioGroup",
  component: RadioGroup,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    orientation: {
      control: "select",
      options: ["vertical", "horizontal"],
    },
    size: {
      control: "select",
      options: ["sm", "md", "lg"],
    },
    variant: {
      control: "select",
      options: ["default", "card"],
    },
  },
  args: {
    defaultValue: "viewer",
    orientation: "vertical",
    size: "md",
    variant: "default",
  },
} satisfies Meta<typeof RadioGroup>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {
  render: (args) => (
    <RadioGroup {...args}>
      <RadioGroup.Item
        value="viewer"
        label="Viewer"
        description="Can inspect records and comments."
      />
      <RadioGroup.Item
        value="editor"
        label="Editor"
        description="Can update records and add comments."
      />
      <RadioGroup.Item
        value="owner"
        label="Owner"
        description="Can manage access and delete records."
      />
    </RadioGroup>
  ),
};

export const Cards: Story = {
  render: () => (
    <RadioGroup
      defaultValue="daily"
      variant="card"
      className="grid w-[420px] grid-cols-3 gap-2"
    >
      <RadioGroup.Item
        value="daily"
        label="Daily"
        description="Every morning"
      />
      <RadioGroup.Item
        value="weekly"
        label="Weekly"
        description="Monday digest"
      />
      <RadioGroup.Item
        value="paused"
        label="Paused"
        description="No alerts"
      />
    </RadioGroup>
  ),
};

export const Horizontal: Story = {
  render: () => (
    <RadioGroup defaultValue="open" orientation="horizontal">
      <RadioGroup.Item value="open" label="Open" />
      <RadioGroup.Item value="pending" label="Pending" />
      <RadioGroup.Item value="closed" label="Closed" disabled />
    </RadioGroup>
  ),
};
