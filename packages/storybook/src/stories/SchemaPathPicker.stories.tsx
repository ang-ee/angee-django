import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { SchemaPathPicker, type SchemaPath, type SchemaPathSchema } from "@angee/ui";

const meta = {
  title: "Views/SchemaPathPicker",
  component: SchemaPathPicker,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SchemaPathPicker>;
export default meta;

const schema: SchemaPathSchema = {
  type: "object",
  $defs: { address: { type: "object", title: "Address", properties: { city: { type: "string", title: "City" } } } },
  properties: {
    title: { type: "string", title: "Title" },
    address: { $ref: "#/$defs/address" },
    entries: { type: "array", title: "Entries", items: { type: "object", properties: { label: { type: "string", title: "Label" } } } },
    attributes: { type: "object", additionalProperties: { type: "string" } },
    codes: { type: "object", patternProperties: { "^item_": { type: "number" } }, additionalProperties: false },
  },
};
function Example() {
  const [path, setPath] = useState<SchemaPath | null>(["entries", 0, "label"]);
  return <div className="max-w-lg space-y-4">
    <SchemaPathPicker schema={schema} value={path} onChange={setPath} />
    <output aria-label="Selected path">{JSON.stringify(path)}</output>
  </div>;
}
export const NestedFields: StoryObj = { render: () => <Example /> };
export const Disabled: StoryObj = {
  render: () => <SchemaPathPicker schema={schema} value={["title"]} onChange={() => {}} disabled />,
};
