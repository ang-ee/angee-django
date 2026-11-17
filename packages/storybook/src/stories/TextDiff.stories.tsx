import type { Meta, StoryObj } from "@storybook/react-vite";
import { ComparisonRows, TextDiff } from "@angee/ui";

const SOURCE = "Doors open at seven. Seating is first come, first served, and the reading starts at half past.";
const DRAFT = "Doors open at eight. Seating is reserved, and the reading starts at half past eight.";
const SOURCE_ES = "Las puertas abren a las siete. La lectura empieza a las siete y media.";
const DRAFT_ES = "Las puertas abren a las ocho. La lectura empieza a las ocho y media.";

const meta = {
  title: "Fragments/TextDiff",
  component: TextDiff,
  parameters: { layout: "padded" },
  args: { before: SOURCE, after: DRAFT },
} satisfies Meta<typeof TextDiff>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: (args) => <div className="max-w-xl text-sm"><TextDiff {...args} /></div>,
};

/** Two sides in a comparison's cells: removals on the left, additions on the right, each with its language. */
export const BeforeAndAfter: Story = {
  render: () => (
    <div className="max-w-3xl text-sm">
      <ComparisonRows
        fieldLabel="Copy"
        columns={[{ key: "source", label: "Source wording" }, { key: "draft", label: "Draft wording" }]}
        rows={[
          {
            key: "en",
            label: "English",
            changed: true,
            cells: {
              source: <TextDiff before={SOURCE} after={DRAFT} side="before" lang="en" />,
              draft: <TextDiff before={SOURCE} after={DRAFT} side="after" lang="en" />,
            },
          },
          {
            key: "es",
            label: "Spanish",
            changed: true,
            cells: {
              source: <TextDiff before={SOURCE_ES} after={DRAFT_ES} side="before" lang="es" />,
              draft: <TextDiff before={SOURCE_ES} after={DRAFT_ES} side="after" lang="es" />,
            },
          },
        ]}
      />
    </div>
  ),
};

export const Unchanged: Story = {
  args: { before: SOURCE, after: SOURCE },
  render: (args) => <div className="max-w-xl text-sm"><TextDiff {...args} /></div>,
};
