import type { Meta, StoryObj } from "@storybook/react-vite";
import { LoadingPanel } from "@angee/ui";

const meta = {
  title: "Fragments/LoadingPanel",
  component: LoadingPanel,
  parameters: { layout: "centered" },
  args: {
    message: "Loading workspace",
  },
} satisfies Meta<typeof LoadingPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

export const State: Story = {
  render: (args) => <LoadingPanel {...args} />,
};

