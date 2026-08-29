import type { Meta, StoryObj } from "@storybook/react-vite";
import { Accordion } from "@angee/ui";

const meta = {
  title: "Primitives/Accordion",
  component: Accordion,
  parameters: {
    layout: "padded",
  },
  argTypes: {
    variant: {
      control: "select",
      options: ["default", "row", "flush"],
    },
  },
  args: {
    variant: "default",
  },
} satisfies Meta<typeof Accordion>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {
  render: ({ variant }) => (
    <Accordion
      className="mx-auto max-w-xl rounded-6 border border-border-subtle bg-sheet px-4"
      defaultValue={["details"]}
      variant={variant}
    >
      <Accordion.Item value="details">
        <Accordion.Header>
          <Accordion.Trigger>
            <Accordion.Icon />
            Details
          </Accordion.Trigger>
        </Accordion.Header>
        <Accordion.Panel>
          View the owner, status, and change history for the selected record.
        </Accordion.Panel>
      </Accordion.Item>
      <Accordion.Item value="activity">
        <Accordion.Header>
          <Accordion.Trigger>
            <Accordion.Icon />
            Activity
          </Accordion.Trigger>
        </Accordion.Header>
        <Accordion.Panel>
          Recent updates, assignments, and comments are grouped in timeline
          order.
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  ),
};

export const MultipleOpen: Story = {
  render: () => (
    <Accordion
      className="mx-auto max-w-xl rounded-6 border border-border-subtle bg-sheet px-4"
      defaultValue={["metadata", "permissions"]}
      multiple
    >
      <Accordion.Item value="metadata">
        <Accordion.Header>
          <Accordion.Trigger>
            <Accordion.Icon />
            Metadata
          </Accordion.Trigger>
        </Accordion.Header>
        <Accordion.Panel>
          Created by Sofia Marin on May 28, with two pending field changes.
        </Accordion.Panel>
      </Accordion.Item>
      <Accordion.Item value="permissions">
        <Accordion.Header>
          <Accordion.Trigger>
            <Accordion.Icon />
            Permissions
          </Accordion.Trigger>
        </Accordion.Header>
        <Accordion.Panel>
          Editors can update content. Reviewers can approve or return drafts.
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  ),
};
