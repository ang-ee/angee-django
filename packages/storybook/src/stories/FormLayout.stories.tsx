import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, FieldControl, FieldLabel, FieldRoot, FormActions, FormFooter, FormGrid, Textarea } from "@angee/ui";

const meta = {
  title: "Forms/FormLayout",
  component: FormGrid,
  parameters: { layout: "padded" },
} satisfies Meta<typeof FormGrid>;

export default meta;

type Story = StoryObj<typeof meta>;

export const TwoColumnGrid: Story = {
  render: () => (
    <div className="mx-auto max-w-3xl space-y-5 rounded-6 border border-border-subtle bg-sheet p-6">
      <FormGrid columns="two">
        <FieldRoot>
          <FieldLabel variant="eyebrow" required>First name</FieldLabel>
          <FieldControl defaultValue="Sofia" />
        </FieldRoot>
        <FieldRoot>
          <FieldLabel variant="eyebrow" required>Last name</FieldLabel>
          <FieldControl defaultValue="Marin" />
        </FieldRoot>
        <FieldRoot span="full">
          <FieldLabel variant="eyebrow" meta="Notifications">Email</FieldLabel>
          <FieldControl type="email" defaultValue="sofia@example.com" />
        </FieldRoot>
        <FieldRoot span="full">
          <FieldLabel variant="eyebrow">Bio</FieldLabel>
          <FieldControl render={<Textarea rows={3} placeholder="A short description…" />} />
        </FieldRoot>
      </FormGrid>
      <FormActions>
        <Button variant="ghost" size="sm">
          Reset
        </Button>
        <Button variant="primary" size="sm">
          Save changes
        </Button>
      </FormActions>
    </div>
  ),
};

export const GridAreas: Story = {
  render: () => (
    <div className="mx-auto max-w-3xl rounded-6 border border-border-subtle bg-sheet p-6">
      <FormGrid
        columns="two"
        areas={["title title", "owner status", "summary summary"]}
      >
        <FieldRoot area="title">
          <FieldLabel variant="eyebrow" required>Title</FieldLabel>
          <FieldControl defaultValue="Q3 review brief" />
        </FieldRoot>
        <FieldRoot area="owner">
          <FieldLabel variant="eyebrow">Owner</FieldLabel>
          <FieldControl defaultValue="Sofia" />
        </FieldRoot>
        <FieldRoot area="status">
          <FieldLabel variant="eyebrow">Status</FieldLabel>
          <FieldControl defaultValue="Active" />
        </FieldRoot>
        <FieldRoot area="summary">
          <FieldLabel variant="eyebrow">Summary</FieldLabel>
          <FieldControl render={<Textarea rows={3} defaultValue="Quarterly operational review." />} />
        </FieldRoot>
      </FormGrid>
    </div>
  ),
};

export const FooterWithNote: Story = {
  render: () => (
    <div className="mx-auto max-w-3xl rounded-6 border border-border-subtle bg-sheet">
      <div className="p-6">
        <FormGrid columns="one">
          <FieldRoot>
            <FieldLabel variant="eyebrow">Title</FieldLabel>
            <FieldControl defaultValue="Q3 review brief" />
          </FieldRoot>
        </FormGrid>
      </div>
      <FormFooter border="top" surface="sheet" note="Unsaved changes" className="px-6 py-3">
        <Button variant="ghost" size="sm">
          Discard
        </Button>
        <Button variant="primary" size="sm">
          Save
        </Button>
      </FormFooter>
    </div>
  ),
};
