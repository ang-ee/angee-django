import type { Meta, StoryObj } from "@storybook/react-vite";
import { NumberField } from "@angee/ui";

const meta = {
  title: "Primitives/NumberField",
  component: NumberField,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    align: {
      control: "select",
      options: ["start", "center", "end"],
    },
    size: {
      control: "select",
      options: ["sm", "md", "lg"],
    },
  },
  args: {
    defaultValue: 12,
    max: 100,
    min: 0,
    step: 1,
  },
} satisfies Meta<typeof NumberField>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {
  render: (args) => (
    <div className="w-44">
      <NumberField {...args} />
    </div>
  ),
};

export const Sizes: Story = {
  render: () => (
    <div className="grid w-52 gap-3">
      <NumberField size="sm" defaultValue={8} min={0} max={20} />
      <NumberField size="md" defaultValue={16} min={0} max={20} />
      <NumberField size="lg" defaultValue={20} min={0} max={20} />
    </div>
  ),
};

export const States: Story = {
  render: () => (
    <div className="grid w-52 gap-3">
      <NumberField defaultValue={24} format={{ style: "percent" }} step={0.05} />
      <NumberField defaultValue={42} invalid />
      <NumberField defaultValue={88} readOnly />
      <NumberField defaultValue={64} showStepper={false} />
    </div>
  ),
};
