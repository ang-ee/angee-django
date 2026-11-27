import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import * as U from "@angee/ui";
import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";
import { createTaskStorySchemas } from "./task-collection-fixture";

// A dialog whose submit is refused, on each surface that collects values in a dialog: after a
// refusal the dialog brings the person to the first refused field, or to its banner when a reason
// names no field, once the fields it locked while submitting take focus again. A test DOM focuses
// targets a browser refuses (an input inside a disabled fieldset, a disabled trigger), so where
// focus lands is read in a browser, and these stories hold one field per control for it.

type ControlKey = "select" | "combobox" | "date" | "time" | "tags" | "relation" | "owner" | "progress" | "object" | "text";

/** One field in the three shapes the surfaces take, with the value each dialog opens on. */
interface Control {
  key: ControlKey;
  value: unknown;
  field: U.DescriptorField;
  /** The typed argument, or null where a typed argument cannot hold the control. */
  arg: U.ActionArg | null;
  schema: Record<string, unknown>;
}

const FRUITS = [{ value: "a", label: "Apple" }, { value: "b", label: "Berry" }];
const PEOPLE = [{ value: "ada", label: "Ada Lovelace" }, { value: "grace", label: "Grace Hopper" }];
// A task of the shared task collection, which its fixture serves in memory.
const TASK = "pm.Task";

/** A field drawn by one widget, alike on every surface. */
function scalar(key: ControlKey, label: string, widget: string, value: unknown, schema: Record<string, unknown>,
  options?: readonly U.WidgetOption[]): Control {
  const choices = options ? { options } : {};
  return {
    key, value,
    field: { name: key, label, widget, ...choices },
    arg: { name: key, label, widget, optional: true, defaultValue: value, ...choices },
    schema: { ...schema, label, widget, ...choices },
  };
}

const CONTROLS: readonly Control[] = [
  scalar("select", "Select", "select", "a", { type: "string", enum: ["a", "b"] }, FRUITS),
  scalar("combobox", "Combobox", "combobox", "b", { type: "string", enum: ["a", "b"] }, FRUITS),
  scalar("date", "Date", "date", "2026-11-20", { type: "string", format: "date" }),
  scalar("time", "Time", "time", "10:00", { type: "string" }),
  scalar("tags", "Tags", "tagInput", ["release", "support"], { type: "array", items: { type: "string" } }),
  {
    key: "relation", value: "task-research",
    field: { name: "relation", label: "Relation", relation: { resource: TASK } },
    arg: { name: "relation", label: "Relation", argKind: "relation", resource: TASK, optional: true, defaultValue: "task-research" },
    schema: { type: "string", label: "Relation", relation: { resource: TASK } },
  },
  scalar("owner", "Owner", "ownerCell", "ada", { type: "string", enum: ["ada", "grace"] }, PEOPLE),
  scalar("progress", "Progress", "progressBar", 40, { type: "integer" }),
  {
    key: "object", value: { street: "1 Main Street", city: "Lyon" },
    field: { name: "object", label: "Object", widget: "object", objectTemplate: [
      { name: "street", label: "Street", widget: "text", kind: "string" },
      { name: "city", label: "City", widget: "text", kind: "string" },
    ] },
    // A typed argument takes no member template, so it cannot be an object field.
    arg: null,
    schema: { type: "object", label: "Object", properties: {
      street: { type: "string", label: "Street" },
      city: { type: "string", label: "City" },
    } },
  },
  scalar("text", "Text", "text", "Quarterly review", { type: "string" }),
];

const VALUES = Object.fromEntries(CONTROLS.map((control) => [control.key, control.value]));
const FIELDS = CONTROLS.map((control) => control.field);
const ARGS = CONTROLS.flatMap((control) => control.arg ? [control.arg] : []);
const SCHEMA = { type: "object", properties: Object.fromEntries(CONTROLS.map((control) => [control.key, control.schema])) };
const KEYS = CONTROLS.map((control) => control.key);
const TYPED_KEYS = CONTROLS.flatMap((control) => control.arg ? [control.key] : []);

const TITLE = "Send the form";
const REASON = "This value was refused.";
const FORM_REASON = "This submit was refused as a whole.";
/** Long enough to see the fields lock, and for the focus to have to wait for them. */
const ANSWER_DELAY_MS = 300;

/** The story's controls: the field its submit refuses, and whether the refusal also gives a reason about the whole submit. */
interface RefusalArgs {
  refuse: ControlKey;
  formReason: boolean;
}

/** `answer`, given after a short real delay, as a server's would be. */
function answered<T>(answer: T): Promise<T> {
  return new Promise((resolve) => { setTimeout(() => resolve(answer), ANSWER_DELAY_MS); });
}

/**
 * The dialog of one surface, open when the story renders; its submit answers in memory with a
 * refusal of `refuse` (and of the whole submit with `formReason`): the mutation dialog's own
 * `invalidFormSubmit`, the action dialogs' an action outcome they read through
 * `actionOutcomeSubmitResult`. The button above reopens it once it is closed.
 */
function RefusedDialog({ surface, refuse, formReason }: RefusalArgs & { surface: "mutation" | "typed" | "schema" }) {
  const [open, setOpen] = useState(true);
  const { widgets } = U.useAppRuntime();
  const whole = formReason ? [FORM_REASON] : [];
  return <div className="grid w-96 gap-3">
    <U.Button onClick={() => setOpen(true)}>Open the dialog</U.Button>
    {surface === "mutation" ? (
      <U.MutationDialog open={open} onOpenChange={setOpen} title={TITLE} fields={FIELDS} initialValues={VALUES}
        submitLabel="Send" submittingLabel="Sending" parseValues={(values) => values}
        onSubmit={() => answered(U.invalidFormSubmit({ fieldErrors: { [refuse]: [REASON] }, formErrors: whole }))} />
    ) : (
      <U.ActionFormDialog open={open} onOpenChange={setOpen} context={{ record: null, selectedIds: [] }} action={{
        id: "send", label: TITLE, submitLabel: "Send",
        args: surface === "typed" ? ARGS : () => jsonSchemaActionArgs(SCHEMA, widgets, { initialValues: VALUES }),
        // As the server answers: its message joins every reason, the one about the whole submit under `__all__`.
        submit: () => answered({ ok: false, message: [REASON, ...whole].join("; "),
          validationErrors: { [refuse]: [REASON], ...(formReason ? { __all__: whole } : {}) } }),
      }} />
    )}
  </div>;
}

const meta = {
  title: "Views/RefusedSubmit",
  parameters: { layout: "centered", angeeFixture: () => ({ schemas: createTaskStorySchemas() }) },
  argTypes: {
    refuse: { control: "select", options: KEYS, description: "The field the submit refuses." },
    formReason: { control: "boolean", description: "Whether the refusal also gives a reason about the whole submit." },
  },
  args: { refuse: "select", formReason: false },
} satisfies Meta<RefusalArgs>;
export default meta;

type Story = StoryObj<RefusalArgs>;

/**
 * A mutation dialog holding a select, a combobox, a date, a time, a tag input, a relation picker,
 * the owner cell, the progress bar, an object (Street and City) and a text, each with a value.
 * Press Send: the fields lock and the button reads Sending for a moment. Then the field `refuse`
 * names shows "This value was refused." under it, the banner below the fields reads "Please fix
 * the highlighted fields:" and that field's label, and focus is on the field's own control (on
 * Street for the object). With `formReason` the banner also reads "This submit was refused as a
 * whole." and focus is on the banner. To send again, cancel and reopen the dialog.
 */
export const MutationDialog: Story = {
  parameters: { owner: [U.MutationDialog] },
  render: (args) => <RefusedDialog surface="mutation" {...args} />,
};

/**
 * An action dialog of typed arguments holding the same controls but the object: a typed argument
 * takes no member template. Press Send: the fields lock and the button shows it is busy for a
 * moment. Then the field `refuse` names shows "This value was refused." under it, the banner reads
 * "Please fix the highlighted fields:" and that field's label, and focus is on the field's own
 * control. With `formReason` the banner also reads "This submit was refused as a whole." and focus
 * is on the banner. To send again, cancel and reopen the dialog.
 */
export const ActionTypedArguments: Story = {
  argTypes: { refuse: { options: TYPED_KEYS } },
  parameters: { owner: [U.ActionFormDialog] },
  render: (args) => <RefusedDialog surface="typed" {...args} />,
};

/**
 * An action dialog whose fields come from a JSON Schema, holding all ten controls in a fieldset it
 * disables while it submits. Press Send: the fieldset is disabled for a moment. Then the field
 * `refuse` names shows "This value was refused." under it, the banner reads "Please fix the
 * highlighted fields:" and that field's label, and focus is on the field's own control (on Street
 * for the object), in the fieldset enabled again. With `formReason` the banner also reads "This
 * submit was refused as a whole." and focus is on the banner. To send again, cancel and reopen the
 * dialog.
 */
export const ActionSchemaArguments: Story = {
  parameters: { owner: [U.ActionFormDialog] },
  render: (args) => <RefusedDialog surface="schema" {...args} />,
};
