import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Button,
  FieldRow,
  FormActions,
  FormFooter,
  FormGrid,
  Input,
  Textarea,
} from "@angee/ui";

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
        <FieldRow label="First name" required>
          <Input defaultValue="Sofia" />
        </FieldRow>
        <FieldRow label="Last name" required>
          <Input defaultValue="Marin" />
        </FieldRow>
        <FieldRow label="Email" meta="Notifications" span="full">
          <Input type="email" defaultValue="sofia@example.com" />
        </FieldRow>
        <FieldRow label="Bio" span="full">
          <Textarea rows={3} placeholder="A short description…" />
        </FieldRow>
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
        <FieldRow area="title" label="Title" required>
          <Input defaultValue="Q3 review brief" />
        </FieldRow>
        <FieldRow area="owner" label="Owner">
          <Input defaultValue="Sofia" />
        </FieldRow>
        <FieldRow area="status" label="Status">
          <Input defaultValue="Active" />
        </FieldRow>
        <FieldRow area="summary" label="Summary">
          <Textarea rows={3} defaultValue="Quarterly operational review." />
        </FieldRow>
      </FormGrid>
    </div>
  ),
};

export const FooterWithNote: Story = {
  render: () => (
    <div className="mx-auto max-w-3xl rounded-6 border border-border-subtle bg-sheet">
      <div className="p-6">
        <FormGrid columns="one">
          <FieldRow label="Title">
            <Input defaultValue="Q3 review brief" />
          </FieldRow>
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
