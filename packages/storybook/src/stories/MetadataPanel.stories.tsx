import type { Meta, StoryObj } from "@storybook/react-vite";
import { Avatar, Badge, MetadataPanel, Tag } from "@angee/base";

const meta = {
  title: "Fragments/MetadataPanel",
  component: MetadataPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof MetadataPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Panel: Story = {
  render: () => (
    <div className="max-w-sm">
      <MetadataPanel
        lead={<Avatar initials="SM" alt="Sofia Marin" />}
        title="Q3 review brief"
        subtitle="notes/q3-review-brief"
        badges={
          <>
            <Badge tone="warning">Draft</Badge>
            <Tag tone="info">Internal</Tag>
          </>
        }
        sections={[
          {
            id: "file",
            title: "File",
            rows: [
              ["Owner", "Sofia Marin"],
              ["Updated", "Today"],
              ["Words", "2,840"],
            ],
          },
          {
            id: "workflow",
            title: "Workflow",
            rows: [
              ["Stage", "Review"],
              ["Approver", "Operations"],
            ],
          },
        ]}
        tabs={[
          { id: "addon", label: "Addon", content: "example.notes" },
          { id: "path", label: "Path", content: "runtime/notes" },
        ]}
      />
    </div>
  ),
};
