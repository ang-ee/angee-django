import type { Meta, StoryObj } from "@storybook/react-vite";
import { SegmentedControl, ToggleGroup } from "@angee/ui";

const meta = {
  title: "Primitives/ToggleGroup",
  component: ToggleGroup,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    size: {
      control: "select",
      options: ["xs", "sm", "md"],
    },
    variant: {
      control: "select",
      options: ["segmented", "toolbar", "card"],
    },
  },
  args: {
    size: "sm",
    variant: "segmented",
  },
} satisfies Meta<typeof ToggleGroup>;

export default meta;

type Story = StoryObj<typeof meta>;

export const SingleSelect: Story = {
  render: ({ size, variant }) => (
    <SegmentedControl
      aria-label="Date range"
      defaultValue="week"
      options={[
        { value: "day", label: "Day" },
        { value: "week", label: "Week" },
        { value: "month", label: "Month" },
      ]}
      size={size}
      variant={variant}
    />
  ),
};

export const MultipleSelect: Story = {
  render: () => (
    <ToggleGroup.Root
      aria-label="Text style"
      defaultValue={["bold", "code"]}
      multiple
      size="md"
      variant="toolbar"
    >
      <ToggleGroup.Item value="bold" aria-label="Bold">
        B
      </ToggleGroup.Item>
      <ToggleGroup.Item value="italic" aria-label="Italic">
        I
      </ToggleGroup.Item>
      <ToggleGroup.Item value="code" aria-label="Code">
        Code
      </ToggleGroup.Item>
    </ToggleGroup.Root>
  ),
};
