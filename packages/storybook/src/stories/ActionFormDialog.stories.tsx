import { useMemo, useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import * as U from "@angee/ui";
import { surfaceFixture } from "./design-system/surface-specimens";

const record = { id: "doc-1" };

// A top-level text and an object field whose members each have a rule; one member is named `type`.
const spec = {
  type: "object",
  properties: {
    code: { type: "string", label: "Code", minLength: 3 },
    retry: {
      type: "object", widget: "object", label: "Retry",
      properties: {
        handle: { type: "string", label: "Handle", minLength: 3 },
        delay: { type: "integer", label: "Delay", maximum: 10 },
        type: { type: "string", label: "Kind", minLength: 2 },
      },
    },
  },
} as const;
const valid = { code: "abc", retry: { handle: "abc", delay: 5, type: "ab" } };

/**
 * An action dialog that checks its own rules, opened on a record. The submitted values show
 * above the button that reopens it.
 */
function ActionDialog({ args }: { args: U.ActionArgs }) {
  const [open, setOpen] = useState(true);
  const [submitted, setSubmitted] = useState<unknown>();
  return <div className="grid w-96 gap-3">
    <U.Button onClick={() => setOpen(true)}>Open the action</U.Button>
    {submitted ? <output><pre className="rounded-6 bg-inset p-3 text-12">{JSON.stringify(submitted, null, 2)}</pre></output> : null}
    <U.ActionFormDialog open={open} onOpenChange={setOpen} context={{ record, selectedIds: [record.id] }}
      action={{ id: "tune", label: "Tune", args,
        submit: async (values) => { setSubmitted(values); return { ok: true, message: "Tuned" }; } }} />
  </div>;
}

/**
 * An action declared with `args`: Reason is required, Note is optional. Typing into Reason and
 * clearing it shows "This field is required." under it and disables the button; typing again
 * removes the message.
 */
function DeclaredArgs() {
  return <ActionDialog args={[
    { name: "reason", label: "Reason", widget: "text" },
    { name: "note", label: "Note", widget: "text", optional: true },
  ]} />;
}

/**
 * A definition with no resolver, its fields from a FormSpec: Code needs 3 characters, and the
 * Retry object's members Handle 3 characters, Delay at most 10 and Kind, a member named `type`,
 * 2 characters. A failing value shows its message under its member as it is typed.
 */
function DefinitionMembers() {
  const { widgets } = U.useAppRuntime();
  const args = useMemo(() => ({ fields: U.deserializeFormSpec(spec, widgets), defaultValues: valid }), [widgets]);
  return <ActionDialog args={args} />;
}

const meta = {
  title: "Views/ActionFormDialog",
  component: ActionDialog,
  parameters: { layout: "centered", angeeFixture: surfaceFixture, owner: [U.ActionFormDialog] },
} satisfies Meta<typeof ActionDialog>;
export default meta;

type Story = StoryObj;

export const RequiredArgument: Story = { render: () => <DeclaredArgs /> };

export const ObjectMemberRules: Story = { render: () => <DefinitionMembers /> };
