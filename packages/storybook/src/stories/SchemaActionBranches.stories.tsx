import { useMemo, useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import * as U from "@angee/ui";
import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";
import { surfaceFixture } from "./design-system/surface-specimens";

const record = { id: "doc-1" };

// Two branches behind a discriminator: each has a required field of its own, both name `shared`
// with a default of their own, and `note` belongs to neither.
const schema = {
  type: "object", discriminator: { propertyName: "mode" }, required: ["mode"],
  properties: {
    mode: { type: "string", enum: ["email", "post"], label: "Deliver by" },
    note: { type: "string", label: "Note" },
  },
  oneOf: [
    {
      properties: {
        mode: { const: "email" },
        address: { type: "string", label: "Address" },
        shared: { type: "string", label: "Subject", default: "By email" },
      },
      required: ["address"],
    },
    {
      properties: {
        mode: { const: "post" },
        street: { type: "string", label: "Street" },
        shared: { type: "string", label: "Subject", default: "By post" },
      },
      required: ["street"],
    },
  ],
};

/**
 * A schema action whose form changes with a choice. Changing the choice shows the new branch's
 * fields at their initial values, with no message on what the person has not touched; a field both
 * branches name takes the new branch's default; a field outside the branches keeps its value; and
 * only the chosen branch is submitted.
 */
function SchemaActionBranches() {
  const [open, setOpen] = useState(true);
  const [submitted, setSubmitted] = useState<unknown>();
  const { widgets } = U.useAppRuntime();
  const args = useMemo(() => jsonSchemaActionArgs(schema, widgets), [widgets]);
  return <div className="grid w-96 gap-3">
    <U.Button onClick={() => setOpen(true)}>Open the action</U.Button>
    {submitted ? <pre className="rounded-6 bg-surface-inset p-3 text-12">{JSON.stringify(submitted, null, 2)}</pre> : null}
    <U.ActionFormDialog open={open} onOpenChange={setOpen} context={{ record, selectedIds: [record.id] }}
      action={{ id: "deliver", label: "Deliver", args,
        submit: async (values) => { setSubmitted(values); return { ok: true, message: "Delivered" }; } }} />
  </div>;
}

const meta = {
  title: "Views/SchemaActionBranches",
  component: SchemaActionBranches,
  parameters: { layout: "centered", angeeFixture: surfaceFixture, owner: [U.ActionFormDialog] },
} satisfies Meta<typeof SchemaActionBranches>;
export default meta;

export const Branches: StoryObj<typeof meta> = {};
