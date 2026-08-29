import type { Meta, StoryObj } from "@storybook/react-vite";
import { MetaGrid, MetaSection } from "@angee/ui";

const meta = {
  title: "Fragments/MetaGrid",
  component: MetaGrid,
  parameters: { layout: "padded" },
} satisfies Meta<typeof MetaGrid>;

export default meta;

type Story = StoryObj;

export const Grid: Story = {
  render: () => (
    <div className="max-w-xl rounded-6 border border-border-subtle bg-sheet p-5">
      <MetaSection title="Record metadata">
        <MetaGrid
          rows={[
            ["Owner", "Platform"],
            ["Updated", "Today"],
            ["Visibility", "Internal"],
            { id: "words", label: "Words", value: "2,840" },
          ]}
        />
      </MetaSection>
    </div>
  ),
};
