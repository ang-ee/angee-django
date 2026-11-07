import type { Meta, StoryObj } from "@storybook/react-vite";
import { LoadingPanel } from "@angee/ui";

// Every shape is a skeleton; `loadingLabel` is the accessible label, never visible copy.
const meta = {
  title: "Fragments/LoadingPanel",
  component: LoadingPanel,
  parameters: { layout: "padded" },
  args: {
    loadingLabel: "Loading workspace",
  },
} satisfies Meta<typeof LoadingPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

export const State: Story = {
  render: (args) => <LoadingPanel {...args} />,
};

export const Inline: Story = {
  args: {
    density: "inline",
    loadingLabel: "Loading folder",
  },
  render: (args) => <LoadingPanel {...args} />,
};

export const Page: Story = {
  args: {
    shape: "page",
    loadingLabel: "Loading page",
  },
  render: (args) => <LoadingPanel {...args} />,
};
