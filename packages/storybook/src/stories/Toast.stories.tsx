import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, useToast, type ToastOptions } from "@angee/base";

const meta = {
  title: "Primitives/Toast",
  parameters: {
    layout: "padded",
  },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

export const Info: Story = {
  render: () => (
    <ToastCanvas>
      <SeedToast
        options={{
          tone: "info",
          title: "Import queued",
          description: "Resource sync will start shortly.",
          duration: 0,
        }}
      />
    </ToastCanvas>
  ),
};

export const Success: Story = {
  render: () => (
    <ToastCanvas>
      <SeedToast
        options={{
          tone: "success",
          title: "Saved",
          description: "The record is ready for the next step.",
          duration: 0,
        }}
      />
    </ToastCanvas>
  ),
};

export const Warning: Story = {
  render: () => (
    <ToastCanvas>
      <SeedToast
        options={{
          tone: "warning",
          title: "Review needed",
          description: "Two imported rows need field confirmation.",
          duration: 0,
        }}
      />
    </ToastCanvas>
  ),
};

export const Error: Story = {
  render: () => (
    <ToastCanvas>
      <SeedToast
        options={{
          tone: "danger",
          title: "Import failed",
          description: "The source returned an invalid response.",
        }}
      />
    </ToastCanvas>
  ),
};

export const WithAction: Story = {
  render: () => (
    <ToastCanvas>
      <SeedToast
        options={{
          tone: "warning",
          title: "Draft changes",
          description: "Review the pending edits before publishing.",
          duration: 0,
          action: {
            label: "Review",
            onClick: () => undefined,
          },
        }}
      />
    </ToastCanvas>
  ),
};

export const PersistentError: Story = {
  render: () => (
    <ToastCanvas>
      <SeedToast
        options={{
          tone: "danger",
          title: "Connection lost",
          description: "Retry after the workspace connection is restored.",
          duration: Infinity,
        }}
      />
    </ToastCanvas>
  ),
};

export const Interactive: Story = {
  render: () => (
    <ToastCanvas>
      <InteractiveToastButton />
    </ToastCanvas>
  ),
};

// The global preview decorator already provides the `ToastProvider`; this is just
// the canvas the seeded toasts render against.
function ToastCanvas({
  children,
}: {
  children?: React.ReactNode;
}): React.ReactElement {
  return <div className="flex min-h-80 items-start">{children}</div>;
}

function SeedToast({ options }: { options: ToastOptions }): null {
  const toast = useToast();
  const shown = React.useRef(false);

  React.useEffect(() => {
    if (shown.current) return;
    shown.current = true;
    toast(options);
  }, [options, toast]);

  return null;
}

function InteractiveToastButton(): React.ReactElement {
  const toast = useToast();

  return (
    <Button
      type="button"
      variant="primary"
      onClick={() => {
        toast.success({
          title: "Saved",
          description: "The record is ready for review.",
        });
      }}
    >
      Fire toast
    </Button>
  );
}
