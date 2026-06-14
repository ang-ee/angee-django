import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph, ListItem, ListPanel, Tag } from "@angee/base";

const meta = {
  title: "Fragments/ListPanel",
  component: ListPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ListPanel>;

export default meta;

type Story = StoryObj;

export const List: Story = {
  render: () => (
    <ListPanel
      actions={
        <Button size="sm" variant="secondary">
          <Glyph name="plus" />
          Queue import
        </Button>
      }
      summary="Recent resource runs"
      title="Imports"
    >
      <ListItem
        actions={
          <Button size="sm" variant="ghost">
            Review
          </Button>
        }
        meta="12,408 rows"
        status={{ label: "Complete", tone: "success" }}
        tags={<Tag tone="brand">Resources</Tag>}
        title="Contacts CSV"
      />
      <ListItem
        meta="884 rows"
        status={{ label: "Running", tone: "info" }}
        tags={<Tag>Delta</Tag>}
        title="Notes backfill"
      />
      <ListItem
        meta="2 failed rows"
        status={{ label: "Needs review", tone: "warning" }}
        title="File attachments"
      />
    </ListPanel>
  ),
};

export const Empty: Story = {
  render: () => (
    <ListPanel empty="No imports have run yet." summary="Recent resource runs" title="Imports" />
  ),
};

