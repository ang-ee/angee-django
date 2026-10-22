import type { Meta, StoryObj } from "@storybook/react-vite";
import { ListStatusMessage } from "@angee/ui";

/**
 * A list's in-flow message row: a note, a refusal or an error that applies to
 * the rows around it. `danger` announces as an alert; anything else is a muted
 * status.
 */
const meta = {
  title: "Fragments/ListStatusMessage",
  component: ListStatusMessage,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ListStatusMessage>;

export default meta;

type Story = StoryObj;

export const Default: Story = {
  render: () => (
    <div className="grid w-96 gap-3 rounded-6 border border-border-subtle bg-sheet p-3">
      <ListStatusMessage>12 of 40 rows are hidden by the active filter.</ListStatusMessage>
      <ListStatusMessage tone="danger">Two rows could not be read: you lack access to their journal.</ListStatusMessage>
      <ListStatusMessage density="row" align="center">No more rows.</ListStatusMessage>
    </div>
  ),
};
