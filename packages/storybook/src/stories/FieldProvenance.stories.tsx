import { useMemo } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { FormProvider, useForm } from "react-hook-form";
import {
  DescriptorFieldList,
  RecordChromeProvider,
  RecordFieldMarksProvider,
  createRouteHref,
  useRecordFieldMarks,
  type DescriptorField,
} from "@angee/ui";

import {
  jsonResponse,
  storySchema,
  testDataResource,
} from "./runtime-fixtures";

const PARTY_ID = "pty_ada";
const STATEMENT_ID = "pst_ada_name";
const fields: readonly DescriptorField[] = [
  { name: "display_name", label: "Name", readOnly: true },
  { name: "language", label: "Language", readOnly: true },
];
const schemas = storySchema(async () => jsonResponse({ data: {} }));
schemas.public!.metadata = { angee: { resources: [
  testDataResource("parties.Party", { schemaName: "public" }),
  testDataResource("parties.PartyStatement", { schemaName: "public" }),
] } };
const runtime = {
  routeHref: createRouteHref([
    { name: "parties.statements", path: "/parties/statements" },
    { name: "parties.statements.record", path: "/parties/statements/$id" },
  ]),
  routesByResource: {
    "parties.PartyStatement": {
      collection: "parties.statements",
      record: { name: "parties.statements.record", param: "id" },
    },
  },
};

const meta = {
  title: "Views/FieldProvenance",
  parameters: { angeeFixture: { schemas, runtime }, layout: "centered" },
} satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

function PartyFieldProvenance() {
  const form = useForm({ defaultValues: { display_name: "Ada Lovelace", language: "English" } });
  const publications = useMemo(() => [{
    model: "parties.Party",
    id: PARTY_ID,
    marks: [{
      field: "display_name",
      label: "Correction",
      tone: "success" as const,
      origin: "person:usr_ada",
      target: { model: "parties.PartyStatement", id: STATEMENT_ID },
    }],
  }], []);
  useRecordFieldMarks(publications);
  return <RecordChromeProvider value={{
    resource: "parties.Party",
    canonicalResource: "parties.Party",
    dataProviderName: "public",
    recordId: PARTY_ID,
    record: { id: PARTY_ID, display_name: "Ada Lovelace", language: "English" },
    formReadOnly: true,
  }}><FormProvider {...form}>
    <div className="w-96 rounded-12 border border-border bg-sheet p-5">
      <DescriptorFieldList fields={fields} />
      <p className="mt-4 text-12 text-fg-muted">
        Hover the Correction badge for its origin; follow it to the applied statement.
      </p>
    </div>
  </FormProvider></RecordChromeProvider>;
}

export const PartyStatement: Story = {
  render: () => (
    <RecordFieldMarksProvider><PartyFieldProvenance /></RecordFieldMarksProvider>
  ),
};
