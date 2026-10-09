import type { Meta, StoryObj } from "@storybook/react-vite";
import { FloatingField } from "@angee/ui";
import { Button } from "@angee/ui";

const meta = {
  title: "Primitives/FloatingField",
  component: FloatingField,
  parameters: { layout: "padded" },
  argTypes: {
    size: { control: "select", options: ["sm", "md", "lg"] },
    invalid: { control: "boolean" },
    disabled: { control: "boolean" },
    required: { control: "boolean" },
  },
  args: {
    label: "Email address",
    size: "md",
    type: "email",
  },
} satisfies Meta<typeof FloatingField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Sizes: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-5">
      <FloatingField label="Small field" size="sm" type="text" />
      <FloatingField label="Medium field" size="md" type="text" />
      <FloatingField label="Large field" size="lg" type="text" />
    </div>
  ),
};

export const States: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-5">
      <FloatingField label="Default" type="text" />
      <FloatingField label="With value" type="text" defaultValue="Ada Lovelace" />
      <FloatingField
        label="Required field"
        type="text"
        required
        description="This field is required to continue."
      />
      <FloatingField
        label="Invalid"
        type="email"
        defaultValue="not-an-email"
        invalid
        error="Please enter a valid email address."
      />
      <FloatingField label="Disabled" type="text" disabled defaultValue="Read only" />
    </div>
  ),
};

export const LoginForm: Story = {
  name: "Login form example",
  render: () => (
    <div className="mx-auto max-w-sm rounded-8 border border-border-subtle bg-sheet p-6 shadow-sm">
      <div className="mb-6 space-y-1">
        <h2 className="text-22 font-semibold text-fg">Sign in</h2>
        <p className="text-13 text-fg-muted">Enter your credentials to continue.</p>
      </div>
      <div className="flex flex-col gap-5">
        <FloatingField
          label="Email"
          type="email"
          autoComplete="email"
        />
        <FloatingField
          label="Password"
          type="password"
          autoComplete="current-password"
        />
        <Button variant="primary" className="w-full" size="lg">
          Continue
        </Button>
      </div>
    </div>
  ),
};

export const ComparisonStackedVsFloating: Story = {
  name: "Stacked vs Floating comparison",
  render: () => (
    <div className="grid max-w-2xl gap-8 sm:grid-cols-2">
      {/* Stacked (existing) */}
      <div className="space-y-4">
        <p className="text-2xs font-semibold uppercase tracking-wide text-fg-muted">
          Stacked (current default)
        </p>
        <div className="space-y-4 rounded-8 border border-border-subtle bg-sheet p-4">
          <div className="grid gap-1.5">
            <label className="text-13 font-medium text-fg">First name</label>
            <input
              type="text"
              placeholder="Ada"
              className="h-input-h w-full rounded-6 border border-border bg-sheet px-2 text-13 text-fg placeholder:text-fg-subtle outline-none focus:border-border-focus focus:focus-ring transition-colors"
            />
          </div>
          <div className="grid gap-1.5">
            <label className="text-13 font-medium text-fg">Last name</label>
            <input
              type="text"
              placeholder="Lovelace"
              className="h-input-h w-full rounded-6 border border-border bg-sheet px-2 text-13 text-fg placeholder:text-fg-subtle outline-none focus:border-border-focus focus:focus-ring transition-colors"
            />
          </div>
          <div className="grid gap-1.5">
            <label className="text-13 font-medium text-fg">Email</label>
            <input
              type="email"
              placeholder="ada@example.com"
              className="h-input-h w-full rounded-6 border border-border bg-sheet px-2 text-13 text-fg placeholder:text-fg-subtle outline-none focus:border-border-focus focus:focus-ring transition-colors"
            />
          </div>
        </div>
      </div>

      {/* Floating */}
      <div className="space-y-4">
        <p className="text-2xs font-semibold uppercase tracking-wide text-fg-muted">
          Floating (new option)
        </p>
        <div className="space-y-4 rounded-8 border border-border-subtle bg-sheet p-4">
          <FloatingField label="First name" type="text" />
          <FloatingField label="Last name" type="text" />
          <FloatingField label="Email" type="email" />
        </div>
      </div>
    </div>
  ),
};
