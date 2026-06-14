import type { Meta, StoryObj } from "@storybook/react-vite";
import { MiniCard, Tag } from "@angee/base";

const meta = {
  title: "Fragments/MiniCard",
  component: MiniCard,
  parameters: { layout: "padded" },
} satisfies Meta<typeof MiniCard>;

export default meta;

type Story = StoryObj;

export const Summary: Story = {
  render: () => (
    <div className="grid max-w-3xl gap-3 sm:grid-cols-2">
      <MiniCard
        icon="file"
        meta="Updated today"
        primaryTag={{ label: "Draft", tone: "warning" }}
        title="Q3 review brief"
        tags={
          <>
            <Tag tone="info">Internal</Tag>
            <Tag>2,840 words</Tag>
          </>
        }
      />
      <MiniCard
        icon="archive"
        meta="Published May 28"
        primaryTag={{ label: "Live", tone: "success" }}
        title="Storage policy memo"
      />
    </div>
  ),
};
