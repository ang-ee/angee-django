import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  AppRuntimeProvider,
  Button,
  MutationDialog,
  baseIcons,
  defaultWidgets,
  deserializeFormSpec,
  mutationDialogValueCodecs,
  type DescriptorField,
  type MutationDialogValues,
} from "@angee/ui";

const fields: readonly DescriptorField[] = [
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
type DirectoryConnectStoryValues = Record<string, unknown> & {
  name: string;
  serverUrl: string;
  username: string;
  password: string;
};

const parseValues = (
  values: MutationDialogValues,
): DirectoryConnectStoryValues => ({
  name: mutationDialogValueCodecs.requiredString(values.name, "name"),
  serverUrl: mutationDialogValueCodecs.requiredString(
    values.serverUrl,
    "serverUrl",
  ),
  username: mutationDialogValueCodecs.requiredString(values.username, "username"),
  password: mutationDialogValueCodecs.verbatimString(values.password, "password"),
});

const meta = {
  title: "Views/MutationDialog",
  component: MutationDialog,
  parameters: { layout: "centered" },
} satisfies Meta<typeof MutationDialog>;

export default meta;

type Story = StoryObj;

function MutationDialogDemo(): React.ReactElement {
  const [submitted, setSubmitted] = React.useState<Record<string, unknown> | null>(
    null,
  );

  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
      <div className="grid gap-4">
        {submitted ? (
          <pre className="max-w-md rounded-6 bg-inset p-3 text-12">
            {JSON.stringify(submitted, null, 2)}
          </pre>
        ) : null}
      </div>
      <MutationDialog
        trigger={<Button variant="primary">Open dialog</Button>}
        title="Connect directory"
        description="Store the account settings and create a synced directory."
        fields={fields}
        submitLabel="Connect"
        submittingLabel="Connecting"
        parseValues={parseValues}
        onSubmit={async (values) => {
          setSubmitted(values);
          return { status: "ok", data: values };
        }}
      />
    </AppRuntimeProvider>
  );
}

export const Dialog: Story = {
  render: () => <MutationDialogDemo />,
};

// An object field whose members each have a rule; one member is named `type`.
const objectFields = deserializeFormSpec({
  type: "object",
  properties: {
    retry: {
      type: "object", widget: "object", label: "Retry",
      properties: {
        handle: { type: "string", label: "Handle", minLength: 3 },
        delay: { type: "integer", label: "Delay", maximum: 10 },
        type: { type: "string", label: "Kind", minLength: 2 },
      },
    },
  },
}, defaultWidgets);

/**
 * An object field's members Handle (3 characters at least), Delay (at most 10) and Kind (a
 * member named `type`, 2 characters at least). A failing value shows its message under its
 * member as it is typed, and Save is disabled while one shows.
 */
function ObjectFieldDemo(): React.ReactElement {
  const [submitted, setSubmitted] = React.useState<Record<string, unknown> | null>(
    null,
  );

  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
      <div className="grid gap-4">
        {submitted ? (
          <output>
            <pre className="max-w-md rounded-6 bg-inset p-3 text-12">
              {JSON.stringify(submitted, null, 2)}
            </pre>
          </output>
        ) : null}
      </div>
      <MutationDialog
        trigger={<Button variant="primary">Open dialog</Button>}
        title="Retry"
        fields={objectFields}
        initialValues={{ retry: { handle: "abc", delay: 5, type: "ab" } }}
        submitLabel="Save"
        parseValues={(values) => values}
        onSubmit={async (values) => {
          setSubmitted(values);
          return { status: "ok", data: values };
        }}
      />
    </AppRuntimeProvider>
  );
}

export const ObjectField: Story = {
  render: () => <ObjectFieldDemo />,
};
