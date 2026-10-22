import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Chip, Glyph, Input, RecordHeader, StatusbarSteps } from "@angee/ui";

const meta = {
  title: "Fragments/RecordHeader",
  component: RecordHeader,
  parameters: { layout: "padded" },
} satisfies Meta<typeof RecordHeader>;

export default meta;

type Story = StoryObj;

export const DetailRecord: Story = {
  render: () => (
    <div className="max-w-4xl overflow-hidden rounded-6 border border-border-subtle">
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

const steps = [
  { value: "DRAFT", label: "Draft" },
  { value: "POSTED", label: "Posted" },
  { value: "PAID", label: "Paid" },
];

/** The record page header: eyebrow (kind · container), display title, facts line, the record's statusbar on the title row. */
export const Default: Story = {
  render: () => (
    <div className="max-w-4xl overflow-hidden rounded-6 border border-border-subtle">
      <RecordHeader
        crumbs="Vendor bill · Purchases"
        title="BILL/2026/00006"
        meta={<><span>Cloudflare, Inc.</span><span>€ 418.60</span><span>due Nov 20, 2026</span></>}
        status={<StatusbarSteps steps={steps} value="POSTED" readOnly />}
      />
    </div>
  ),
};

/** An editable title is a field, not a heading: `headingLevel={null}`. */
export const EditableTitle: Story = {
  render: () => (
    <div className="max-w-4xl overflow-hidden rounded-6 border border-border-subtle">
      <RecordHeader
        crumbs="Page · Accounts Payable / Supplier contracts"
        headingLevel={null}
        title={<Input aria-label="Page title" defaultValue="Cloudflare, Inc. agreement" className="w-full" />}
        meta="edited Oct 18, 2026 by admin · 412 words"
      />
    </div>
  ),
};

/** Compact density titles in the title role: a sticky band, an explorer subject. */
export const Compact: Story = {
  render: () => (
    <div className="max-w-4xl overflow-hidden rounded-6 border border-border-subtle">
      <RecordHeader
        density="compact"
        crumbs="File · AP drive / 2026 / October"
        title="v6-cloudflare-9311-notax.xml"
        meta="XML · 5.8 KB · uploaded Oct 18, 2026 by admin"
        status={{ label: "In trash", tone: "warning" }}
      />
    </div>
  ),
};

/** The extra slots: a lead row above the title, adornments after it, an extra row after the facts. */
export const Slots: Story = {
  render: () => (
    <div className="max-w-4xl overflow-hidden rounded-6 border border-border-subtle">
      <RecordHeader
        crumbs="Run · Invoice review"
        lead={<Chip tone="warning" mark="dot">Waiting on you</Chip>}
        title="Invoice review"
        titleAdornments={<Chip tone="info">#3</Chip>}
        meta="started Oct 20, 2026 09:12 · 4 min"
        extra={<span className="text-12 text-fg-muted">Triggered by the upload of v6-cloudflare-9311-notax.xml</span>}
      />
    </div>
  ),
};
