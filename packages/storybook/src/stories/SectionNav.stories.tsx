import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  CountBadge,
  Page,
  PageBody,
  PageHeader,
  PageToolbar,
  SectionNav,
  type SectionNavItem,
} from "@angee/ui";

const items: readonly SectionNavItem[] = [
  { id: "overview", label: "Overview", href: "#overview", active: true },
  { id: "problems", label: "Problems", href: "#problems" },
  { id: "meds", label: "Medications", href: "#meds" },
  { id: "files", label: "Files", href: "#files" },
];

const meta = {
  title: "Page/SectionNav",
  component: SectionNav,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof SectionNav>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Horizontal: Story = {
  render: () => (
    <Page height="auto" className="w-[52rem] overflow-hidden rounded-6 border border-border-subtle">
      <PageHeader density="compact" title="Patient record" />
      <PageToolbar start={<SectionNav items={items} />} />
      <PageBody>
        <p className="text-13 text-fg-muted">
          4 active problems and 7 medications are on file.
        </p>
      </PageBody>
    </Page>
  ),
};

export const Vertical: Story = {
  render: () => (
    <div className="w-64 rounded-6 border border-border-subtle bg-sheet p-3">
      <SectionNav
        orientation="vertical"
        items={[
          {
            id: "inbox",
            label: (
              <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
                Inbox
                <CountBadge value={18} />
              </span>
            ),
            active: true,
          },
          { id: "assigned", label: "Assigned", href: "#assigned" },
          { id: "closed", label: "Closed", href: "#closed" },
        ]}
      />
    </div>
  ),
};
