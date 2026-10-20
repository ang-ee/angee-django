import type { Meta, StoryObj } from "@storybook/react-vite";
import { Alert, StepList } from "@angee/ui";

/**
 * Vertical steps: the trigger, done steps with outcomes, the current step, muted
 * planned and optional paths, a stopped path. Tones follow `statusTone`.
 */
const meta = {
  title: "Communication/StepList",
  component: StepList,
  parameters: { layout: "padded" },
} satisfies Meta<typeof StepList>;

export default meta;

type Story = StoryObj;

/** A run's steps at compact density: one line each, a callout only on the step that needs it. */
export const Default: Story = {
  render: () => (
    <div className="w-96">
      <StepList items={[
        { id: "trigger", state: "trigger", title: "Document uploaded", timestamp: "2026-10-20T09:00:00Z" },
        { id: "import", state: "done", title: "Import the document", outcome: "6 changes", timestamp: "2026-10-20T09:00:12Z" },
        { id: "match", state: "done", title: "Match the purchase order", outcome: "PO 4471", timestamp: "2026-10-20T09:00:40Z" },
        { id: "review", state: "current", title: "Review the lines", detail: "Waiting on you",
          children: <Alert tone="warning" title="Two lines have no tax code">Pick a tax code before posting.</Alert> },
        { id: "post", state: "planned", title: "Post the bill" },
        { id: "pay", state: "optional", title: "Schedule the payment" },
      ]} />
    </div>
  ),
};

/** A stopped run: the failed step carries the danger tone and the rest of the path is stopped. */
export const Stopped: Story = {
  render: () => (
    <div className="w-96">
      <StepList items={[
        { id: "import", state: "done", title: "Import the document", outcome: "6 changes" },
        { id: "match", state: "current", tone: "danger", title: "Match the purchase order", outcome: "No purchase order found",
          children: <Alert tone="danger" title="Run stopped">The vendor has no open purchase orders.</Alert> },
        { id: "review", state: "stopped", title: "Review the lines" },
        { id: "post", state: "stopped", title: "Post the bill" },
      ]} />
    </div>
  ),
};
