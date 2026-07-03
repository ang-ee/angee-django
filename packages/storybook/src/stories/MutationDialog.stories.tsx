import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  AppRuntimeProvider,
  Button,
  MutationDialog,
  baseIcons,
  defaultWidgets,
  type MutationDialogField,
} from "@angee/ui";

const fields: readonly MutationDialogField[] = [
  { name: "name", label: "Name", required: true },
  {
    name: "serverUrl",
    label: "Server URL",
    placeholder: "https://example.test/dav",
    required: true,
  },
  { name: "username", label: "Username", required: true },
  { name: "password", label: "Password", widget: "password", required: true },
];

const meta = {
  title: "Views/MutationDialog",
  component: MutationDialog,
  parameters: { layout: "centered" },
} satisfies Meta<typeof MutationDialog>;

export default meta;

type Story = StoryObj;

function MutationDialogDemo(): React.ReactElement {
  const [open, setOpen] = React.useState(true);
  const [submitted, setSubmitted] = React.useState<Record<string, unknown> | null>(
    null,
  );

  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
      <div className="grid gap-4">
        <Button variant="primary" onClick={() => setOpen(true)}>
          Open dialog
        </Button>
        {submitted ? (
          <pre className="max-w-md rounded-6 bg-inset p-3 text-xs">
            {JSON.stringify(submitted, null, 2)}
          </pre>
        ) : null}
      </div>
      <MutationDialog
        open={open}
        onOpenChange={setOpen}
        title="Connect directory"
        description="Store the account settings and create a synced directory."
        fields={fields}
        submitLabel="Connect"
        submittingLabel="Connecting"
        onSubmit={async (values) => {
          setSubmitted(values);
        }}
      />
    </AppRuntimeProvider>
  );
}

export const Dialog: Story = {
  render: () => <MutationDialogDemo />,
};
