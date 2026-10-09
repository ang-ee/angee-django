import type { Meta, StoryObj } from "@storybook/react-vite";
import { InlineField } from "@angee/ui";
import { Button } from "@angee/ui";

/**
 * InlineField — an input where the label lives inside the box.
 *
 * When the field is empty and unfocused the label sits centred, looking like a
 * placeholder. On focus or when the field has a value it shrinks and rises to
 * the top of the box, leaving room for the typed text below it.
 *
 * The interaction is pure CSS — no JavaScript state, no ResizeObserver.
 * Motion is gated behind prefers-reduced-motion.
 */
const meta = {
  title: "Primitives/InlineField",
  component: InlineField,
  parameters: { layout: "padded" },
  argTypes: {
    size:     { control: "select", options: ["sm", "md", "lg"] },
    invalid:  { control: "boolean" },
    disabled: { control: "boolean" },
    required: { control: "boolean" },
  },
  args: {
    label: "Email address",
    size: "md",
    type: "email",
  },
} satisfies Meta<typeof InlineField>;

export default meta;
type Story = StoryObj<typeof meta>;

// ─── Playground ───────────────────────────────────────────────────────────────

export const Playground: Story = {};

// ─── Sizes ────────────────────────────────────────────────────────────────────

export const Sizes: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-4">
      <InlineField label="Small (44px)" size="sm" type="text" />
      <InlineField label="Medium (56px)" size="md" type="text" />
      <InlineField label="Large (64px)" size="lg" type="text" />
    </div>
  ),
};

// ─── States ───────────────────────────────────────────────────────────────────

export const States: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-4">
      <InlineField label="Default (empty)" type="text" />
      <InlineField label="With value" type="text" defaultValue="Ada Lovelace" />
      <InlineField
        label="Required"
        type="text"
        required
        description="This field is required."
      />
      <InlineField
        label="Invalid"
        type="email"
        defaultValue="not-an-email"
        invalid
        error="Please enter a valid email address."
      />
      <InlineField label="Disabled" type="text" disabled defaultValue="Read only value" />
    </div>
  ),
};

// ─── Login form ───────────────────────────────────────────────────────────────

export const LoginForm: Story = {
  name: "Login form",
  render: () => (
    <div className="mx-auto max-w-sm rounded-8 border border-border-subtle bg-sheet p-6 shadow-sm">
      <div className="mb-5 space-y-1">
        <h2 className="text-22 font-semibold text-fg">Sign in</h2>
        <p className="text-13 text-fg-muted">Enter your credentials to continue.</p>
      </div>
      <div className="flex flex-col gap-3">
        <InlineField label="Email" type="email" autoComplete="email" />
        <InlineField label="Password" type="password" autoComplete="current-password" />
        <Button variant="primary" className="mt-1 w-full" size="lg">
          Continue
        </Button>
      </div>
    </div>
  ),
};

// ─── Stacked vs Inline comparison ────────────────────────────────────────────

export const ComparisonStackedVsInline: Story = {
  name: "Stacked vs Inline comparison",
  render: () => (
    <div className="grid max-w-2xl gap-8 sm:grid-cols-2">

      {/* Stacked */}
      <div className="space-y-3">
        <p className="text-2xs font-semibold uppercase tracking-wide text-fg-muted">
          Stacked (default)
        </p>
        <div className="space-y-3 rounded-8 border border-border-subtle bg-sheet p-4">
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

      {/* Inline */}
      <div className="space-y-3">
        <p className="text-2xs font-semibold uppercase tracking-wide text-fg-muted">
          Inline (label inside)
        </p>
        <div className="space-y-3 rounded-8 border border-border-subtle bg-sheet p-4">
          <InlineField label="First name" type="text" />
          <InlineField label="Last name" type="text" />
          <InlineField label="Email" type="email" />
        </div>
      </div>

    </div>
  ),
};
