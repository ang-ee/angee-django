import * as React from "react";
import type { AngeeSchemaMetadata } from "@angee/metadata";
import { testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, ManageAccessDialog, type RecordAccessEntry } from "@angee/ui";

import { RuntimeFixture, jsonResponse, storySchema } from "./runtime-fixtures";

const metadata = {
  angee: {
    resources: [{
      schemaName: "public",
      modelLabel: "example.Subject",
      appLabel: "example",
      modelName: "Subject",
      roots: { list: "subjects" },
      typeNames: { node: "SubjectType" },
      recordRepresentation: "name",
      subjectField: "assignmentSubject",
      grantable: [],
      capabilities: ["list"],
      fields: [],
      query: testResourceQuery({
        fields: {
          id: testQueryField("id", { scalar: "ID", filter: null }),
          name: testQueryField("name", { filter: null }),
          assignmentSubject: testQueryField("assignmentSubject", { filter: null }),
        },
      }),
      aggregateFields: [],
    }],
  },
} satisfies AngeeSchemaMetadata;

const schemas = storySchema(async () => jsonResponse({
  data: {
    subjects: [
      { id: "subject-1", name: "Ada Lovelace", assignmentSubject: "auth/user:ada" },
      { id: "subject-2", name: "Reviewers", assignmentSubject: "auth/group:7#member" },
    ],
  },
}));
schemas.public = { ...schemas.public!, metadata };

const entries: readonly RecordAccessEntry[] = [{
  id: "project-1:reader:auth/user:ada",
  targetId: "project-1",
  relation: "reader",
  subject: "auth/user:ada",
  subjectType: "auth/user",
  label: "Ada Lovelace",
}];

const meta = {
  title: "Views/ManageAccessDialog",
  component: ManageAccessDialog,
  parameters: { layout: "centered" },
} satisfies Meta<typeof ManageAccessDialog>;

export default meta;

type Story = StoryObj;

function DialogStory({
  rows = entries,
  fetching = false,
  error = null,
}: {
  rows?: readonly RecordAccessEntry[];
  fetching?: boolean;
  error?: Error | null;
}): React.ReactElement {
  const [open, setOpen] = React.useState(true);
  return (
    <RuntimeFixture schemas={schemas}>
      <ManageAccessDialog
        open={open}
        onOpenChange={setOpen}
        trigger={<Button>Share project</Button>}
        label="Apollo"
        targetIds={["project-1"]}
        grantable={[{
          relation: "reader",
          permission: "share",
          subjects: [{ type: "example/subject", relation: null, resource: "example.Subject" }],
        }]}
        entries={rows}
        fetching={fetching}
        error={error}
        onRetry={() => undefined}
        onGrant={async () => true}
        onRevoke={async () => undefined}
      />
    </RuntimeFixture>
  );
}

export const Populated: Story = { render: () => <DialogStory /> };
export const Empty: Story = { render: () => <DialogStory rows={[]} /> };
export const Loading: Story = { render: () => <DialogStory rows={[]} fetching /> };
export const Error: Story = {
  render: () => <DialogStory rows={[]} error={new globalThis.Error("Access could not be loaded.")} />,
};
