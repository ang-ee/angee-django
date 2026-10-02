import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { FormProvider, useForm } from "react-hook-form";
import { Button, DescriptorFieldList, type DescriptorField } from "@angee/ui";

import { RuntimeRegistryFixture } from "./runtime-fixtures";

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
  const form = useForm<Record<string, unknown>>({ defaultValues: { title: "Example", details: true, note: "" } });
  const [submitted, setSubmitted] = React.useState<Record<string, unknown>>();
  return <RuntimeRegistryFixture><FormProvider {...form}>
    <form className="grid w-96 gap-4" onSubmit={form.handleSubmit(setSubmitted)}>
      <DescriptorFieldList fields={fields} />
      <div className="flex gap-2">
        <Button type="submit">Save</Button>
        <Button type="button" variant="secondary" onClick={() => form.setError("title", { message: "Choose a different title." })}>Show error</Button>
      </div>
      {submitted ? <pre className="rounded-6 bg-inset p-3 text-xs">{JSON.stringify(submitted, null, 2)}</pre> : null}
    </form>
  </FormProvider></RuntimeRegistryFixture>;
}

export const Fields: StoryObj = { render: () => <DescriptorFieldListDemo /> };
