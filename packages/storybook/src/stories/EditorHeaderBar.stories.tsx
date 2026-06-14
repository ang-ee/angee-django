import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, EditorHeaderBar, Glyph, PageHeader } from "@angee/base";

const meta = {
  title: "Fragments/EditorHeaderBar",
  component: EditorHeaderBar,
  parameters: { layout: "padded" },
} satisfies Meta<typeof EditorHeaderBar>;

export default meta;

type Story = StoryObj;

export const Header: Story = {
  render: () => (
    <div className="overflow-hidden rounded-md border border-border-subtle">
      <PageHeader>
        <EditorHeaderBar
          actions={
            <Button size="sm" variant="ghost">
              <Glyph name="archive" />
              Archive
            </Button>
          }
          onCancel={() => undefined}
          onSubmit={() => undefined}
          subtitle="Production API source"
          tags={["Draft", "Resources"]}
          title="Edit source"
        />
      </PageHeader>
    </div>
  ),
};

export const Saving: Story = {
  render: () => (
    <div className="overflow-hidden rounded-md border border-border-subtle">
      <PageHeader>
        <EditorHeaderBar
          onCancel={() => undefined}
          onSubmit={() => undefined}
          saving
          subtitle="Save is pending"
          tags={["Dirty"]}
          title="Edit source"
        />
      </PageHeader>
    </div>
  ),
};

