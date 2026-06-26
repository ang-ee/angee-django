import type { Meta, StoryObj } from "@storybook/react-vite";
import { BrandButton, BrandButtonGroup, Glyph } from "@angee/ui";

const meta = {
  title: "Fragments/BrandButton",
  component: BrandButton,
  parameters: { layout: "centered" },
} satisfies Meta<typeof BrandButton>;

export default meta;

type Story = StoryObj;

export const Button: Story = {
  render: () => (
    <div className="w-80">
      <BrandButton
        description="Use your workspace identity"
        icon={<Glyph name="angee" />}
        label="Continue with Angee"
        tone="brand"
        variant="brand"
      />
    </div>
  ),
};

export const Group: Story = {
  render: () => (
    <BrandButtonGroup className="w-80">
      <BrandButton icon={<Glyph name="angee" />} label="Continue with Angee" tone="brand" />
      <BrandButton description="Use a protected account" icon={<Glyph name="auth" />} label="Enterprise SSO" />
      <BrandButton error="This provider is not enabled for the workspace." errorId="provider-error" label="Continue with provider" />
    </BrandButtonGroup>
  ),
};

