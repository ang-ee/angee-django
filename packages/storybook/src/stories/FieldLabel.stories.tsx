import type { Meta, StoryObj } from "@storybook/react-vite";
import { FieldDescription, FieldError, FieldLabel, FieldRoot, Input, Select } from "@angee/ui";

/**
 * The one field anatomy: `FieldRoot` owns layout and validity, `FieldLabel`
 * the label (standard in dialogs and settings; the eyebrow on the form sheet)
 * with its required mark, optional hint and meta note.
 */
const meta = {
  title: "Primitives/FieldLabel",
  component: FieldLabel,
  parameters: { layout: "padded", owner: [FieldRoot, FieldLabel, FieldDescription, FieldError] },
} satisfies Meta<typeof FieldLabel>;

export default meta;

type Story = StoryObj;

/** Standard labels in a dialog: required, optional, with a description. */
export const Default: Story = {
  render: () => (
    <div className="grid w-80 gap-4">
      <FieldRoot>
        <FieldLabel htmlFor="vendor" required>Vendor</FieldLabel>
        <Input id="vendor" placeholder="Cloudflare, Inc." />
        <FieldDescription>The party the bill is from.</FieldDescription>
      </FieldRoot>
      <FieldRoot>
        <FieldLabel htmlFor="reference" optional="optional">Reference</FieldLabel>
        <Input id="reference" placeholder="PO 4471" />
      </FieldRoot>
      <FieldRoot invalid>
        <FieldLabel htmlFor="amount" required>Amount</FieldLabel>
        <Input id="amount" defaultValue="-12" aria-invalid />
        <FieldError match>Enter a positive amount.</FieldError>
      </FieldRoot>
    </div>
  ),
};

/** The eyebrow variant the form sheet uses, with a meta note at the label's end. */
export const Eyebrow: Story = {
  render: () => (
    <div className="grid w-80 gap-4">
      <FieldRoot>
        <FieldLabel htmlFor="journal" variant="eyebrow" meta="from the company">Journal</FieldLabel>
        <Select id="journal" aria-label="Journal" defaultValue="purchases" options={[{ value: "purchases", label: "Purchases" }, { value: "bank", label: "Bank" }]} />
      </FieldRoot>
      <FieldRoot>
        <FieldLabel htmlFor="due" variant="eyebrow">Due date</FieldLabel>
        <Input id="due" type="date" defaultValue="2026-11-20" />
      </FieldRoot>
    </div>
  ),
};
