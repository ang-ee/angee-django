import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Badge,
  Chip,
  Page,
  PageAside,
  PageBody,
  PageHeader,
  SectionNav,
  type SectionNavItem,
} from "@angee/ui";

const navItems: readonly SectionNavItem[] = [
  { id: "overview", label: "Overview", href: "#overview", active: true },
  { id: "activity", label: "Activity", href: "#activity" },
  { id: "files", label: "Files", href: "#files" },
];

const meta = {
  title: "Page/PageAside",
  component: PageAside,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof PageAside>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Inspector: Story = {
  render: () => (
    <Page className="h-[30rem] w-[58rem] overflow-hidden rounded-6 border border-border-subtle">
      <PageHeader density="compact" title="Patient record" />
      <div className="flex min-h-0 flex-1">
        <PageBody>
          <div className="max-w-2xl space-y-4">
            <section className="rounded-6 border border-border-subtle bg-sheet p-4">
              <h2 className="text-sm font-semibold text-fg">Clinical summary</h2>
              <p className="mt-2 text-13 leading-5 text-fg-2">
                Follow-up visit with open medication review and two pending orders.
              </p>
            </section>
            <section className="rounded-6 border border-border-subtle bg-sheet p-4">
              <h2 className="text-sm font-semibold text-fg">Latest vitals</h2>
              <p className="mt-2 text-13 leading-5 text-fg-2">
                BP 144/92, pulse 76, HbA1c pending.
              </p>
            </section>
          </div>
        </PageBody>
        <PageAside collapse="never" width="lg">
          <div className="space-y-5">
            <section>
              <h2 className="text-sm font-semibold text-fg">Details</h2>
              <p className="mt-1 text-13 text-fg-muted">MRN 481029</p>
            </section>
            <div className="flex flex-wrap gap-1.5">
              <Badge tone="danger">Allergy</Badge>
              <Badge tone="warning">Review due</Badge>
              <Chip tone="info">BlueCross PPO</Chip>
            </div>
            <SectionNav orientation="vertical" items={navItems} />
          </div>
        </PageAside>
      </div>
    </Page>
  ),
};
