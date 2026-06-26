import type { Meta, StoryObj } from "@storybook/react-vite";
import { Slider } from "@angee/ui";

const meta = {
  title: "Primitives/Slider",
  component: Slider,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    size: {
      control: "select",
      options: ["sm", "md", "lg"],
    },
    tone: {
      control: "select",
      options: ["brand", "success", "warning", "danger"],
    },
    orientation: {
      control: "select",
      options: ["horizontal", "vertical"],
    },
  },
  args: {
    defaultValue: 48,
    max: 100,
    min: 0,
    showValue: true,
    size: "md",
    tone: "brand",
  },
} satisfies Meta<typeof Slider>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {
  render: (args) => (
    <div className="w-80">
      <Slider {...args} />
    </div>
  ),
};

export const Range: Story = {
  render: () => (
    <div className="w-80 space-y-4">
      <Slider
        defaultValue={[20, 72]}
        max={100}
        min={0}
        minStepsBetweenValues={5}
        showValue
        thumbLabels={["Minimum", "Maximum"]}
      />
      <Slider
        defaultValue={[35, 90]}
        max={100}
        min={0}
        showValue
        tone="success"
      />
    </div>
  ),
};

export const Vertical: Story = {
  render: () => (
    <div className="flex h-44 items-center gap-8">
      <Slider
        defaultValue={32}
        orientation="vertical"
        showValue
        thumbLabel="Volume"
      />
      <Slider
        defaultValue={[18, 76]}
        orientation="vertical"
        showValue
        thumbLabels={["Low", "High"]}
        tone="warning"
      />
    </div>
  ),
};
