import type { Meta, StoryObj } from "@storybook/react-vite";
import { Alert, Banner, Button, FILLS } from "@angee/ui";

const meta = {
  title: "Primitives/Alert",
  component: Alert,
  parameters: {
    layout: "padded",
  },
  argTypes: {
    tone: {
      control: "select",
      options: ["info", "success", "warning", "danger"],
    },
    variant: {
      control: "select",
      options: [...FILLS],
    },
    format: {
      control: "select",
      options: ["alert", "banner"],
    },
  },
  args: {
    children: "Changes are saved automatically after validation completes.",
    tone: "info",
    variant: "soft",
    format: "alert",
    title: "Autosave enabled",
  },
} satisfies Meta<typeof Alert>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Tones: Story = {
  render: () => (
    <div className="grid max-w-2xl gap-3">
      <Alert tone="info" title="Information">This record has pending updates.</Alert>
      <Alert tone="success" title="Synced">The latest changes are available.</Alert>
      <Alert tone="warning" title="Review needed">Some fields need confirmation.</Alert>
      <Alert tone="danger" title="Failed">The import could not be completed.</Alert>
    </div>
  ),
};

export const Fills: Story = {
  render: () => (
    <div className="grid max-w-2xl gap-3">
      {FILLS.map((variant) => (
        <Alert key={variant} tone="info" variant={variant} title={variant}>
          The same tone across every fill emphasis.
        </Alert>
      ))}
    </div>
  ),
};

export const WithActions: Story = {
  render: () => (
    <Alert
      tone="warning"
      title="Draft changes"
      actions={<Button size="sm">Review</Button>}
    >
      Review the draft before publishing.
    </Alert>
  ),
};

export const BannerSurface: Story = {
  render: () => (
    <div className="max-w-3xl overflow-hidden rounded-6 border border-border-subtle bg-sheet">
      <Banner tone="success" title="Workspace synced" onDismiss={() => undefined}>
        All records are current.
      </Banner>
      <div className="p-6 text-13 text-fg-muted">Record content</div>
    </div>
  ),
};
