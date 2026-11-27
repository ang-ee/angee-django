import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { ActionFormProvider, Button, DescriptorFieldList, useActionForm, type DescriptorField } from "@angee/ui";

const fields: readonly DescriptorField[] = [
  { name: "title", label: "Title", description: "This value belongs to the surrounding form." },
  { name: "details", label: "Show details", widget: "switch" },
  { name: "note", label: "Note", widget: "textarea", showWhen: (values) => values.details === true },
];

const meta = {
  title: "Views/DescriptorFieldList",
  component: DescriptorFieldList,
  parameters: { layout: "centered" },
} satisfies Meta<typeof DescriptorFieldList>;
export default meta;

function DescriptorFieldListDemo(): React.ReactElement {
  const [submitted, setSubmitted] = React.useState<Record<string, unknown>>();
  // The list lives in an action form: its provider carries editor validation and the preset memory.
  const { form, run } = useActionForm<Record<string, unknown>>({
    defaultValues: { title: "Example", details: true, note: "" },
    shownFieldNames: fields.map((field) => field.name),
    submit: (values) => { setSubmitted(values); return { status: "ok", data: null }; },
  });
  return <ActionFormProvider {...form}>
    <form className="grid w-96 gap-4" onSubmit={(event) => { event.preventDefault(); void run(); }}>
      <DescriptorFieldList fields={fields} />
      <div className="flex gap-2">
        <Button type="submit">Save</Button>
        <Button type="button" variant="secondary" onClick={() => form.setError("title", { message: "Choose a different title." })}>Show error</Button>
      </div>
      {submitted ? <pre className="rounded-6 bg-inset p-3 text-12">{JSON.stringify(submitted, null, 2)}</pre> : null}
    </form>
  </ActionFormProvider>;
}

export const Fields: StoryObj = { render: () => <DescriptorFieldListDemo /> };
