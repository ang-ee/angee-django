import type { Meta, StoryObj } from "@storybook/react-vite";
import { Glyph, SectionTabs, type SectionTabItem } from "@angee/base";

const items: readonly SectionTabItem[] = [
  { id: "overview", label: "Overview", href: "#overview", count: 3 },
  { id: "activity", label: "Activity", href: "#activity", icon: <Glyph name="activity" /> },
  { id: "files", label: "Files", href: "#files", count: 12 },
  { id: "settings", label: "Settings", disabled: true },
];

const meta = {
  title: "Fragments/SectionTabs",
  component: SectionTabs,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SectionTabs>;

export default meta;

type Story = StoryObj;

export const PillTabs: Story = {
  render: () => (
    <div className="max-w-3xl">
      <SectionTabs items={items} defaultValue="overview" />
    </div>
  ),
};

export const PageTabs: Story = {
  render: () => (
    <div className="max-w-3xl overflow-hidden rounded-md border border-border-subtle bg-sheet">
      <SectionTabs items={items} defaultValue="activity" variant="page" />
      <div className="p-4 text-13 text-fg-muted">Selected section content</div>
    </div>
  ),
};
