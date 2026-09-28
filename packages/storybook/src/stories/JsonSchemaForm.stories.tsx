import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { FormProvider, useForm } from "react-hook-form";
import { Button, DescriptorFieldList, type DescriptorField } from "@angee/ui";
import { createJsonSchemaResolver } from "@angee/ui/views/json-schema";

import { RuntimeRegistryFixture } from "./runtime-fixtures";

type Values = { title: string; email: string };
const resolver = createJsonSchemaResolver<Values>({
  type: "object", required: ["title", "email"], additionalProperties: false,
  properties: {
    title: { type: "string", minLength: 3 },
    email: { type: "string", format: "email" },
  },
});
const fields: readonly DescriptorField[] = [
  { name: "title", label: "Title", description: "Enter at least three characters." },
  { name: "email", label: "Email", description: "Validated with the JSON Schema email format." },
];

function JsonSchemaForm(): React.ReactElement {
  const form = useForm<Values>({ defaultValues: { title: "", email: "" }, resolver });
  const [accepted, setAccepted] = React.useState<Values>();
  return <RuntimeRegistryFixture><FormProvider {...form}>
    <form className="grid w-96 gap-4" onSubmit={form.handleSubmit(setAccepted)}>
      <DescriptorFieldList fields={fields} />
      <Button type="submit">Validate</Button>
      {accepted ? <pre className="rounded-6 bg-surface-inset p-3 text-xs">{JSON.stringify(accepted, null, 2)}</pre> : null}
    </form>
  </FormProvider></RuntimeRegistryFixture>;
}

const meta = { title: "Views/JsonSchemaForm", component: JsonSchemaForm, parameters: { layout: "centered" } } satisfies Meta<typeof JsonSchemaForm>;
export default meta;

export const SchemaValidation: StoryObj<typeof meta> = {};
