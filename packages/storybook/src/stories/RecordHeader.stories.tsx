import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph, RecordHeader } from "@angee/base";

const meta = {
  title: "Fragments/RecordHeader",
  component: RecordHeader,
  parameters: { layout: "padded" },
} satisfies Meta<typeof RecordHeader>;

export default meta;

type Story = StoryObj;

export const DetailRecord: Story = {
  render: () => (
    <div className="max-w-4xl overflow-hidden rounded-md border border-border-subtle">
      <RecordHeader
        crumbs="Notes / Operational brief"
        description="Quarterly review of operational notes, release briefs, and owner follow-ups."
        icon="file"
        meta="Owned by Sofia - Updated today at 10:42"
        status={{ label: "Draft", tone: "warning" }}
        title="Q3 review brief"
        type="Note"
        actions={
          <>
            <Button size="sm" variant="secondary">
              <Glyph name="archive" />
              Archive
            </Button>
            <Button size="sm" variant="primary">
              Save
            </Button>
          </>
        }
      />
    </div>
  ),
};
