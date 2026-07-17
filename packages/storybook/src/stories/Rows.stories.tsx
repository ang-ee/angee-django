import { useState, type ReactElement } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import type { AngeeSchemaMetadata } from "@angee/metadata";
import {
  LabeledDescriptorField,
  type FormSpecFieldDescriptor,
  type RowsValue,
} from "@angee/ui";

import { RuntimeFixture, jsonResponse, storySchema } from "./runtime-fixtures";

const rowTemplate: readonly FormSpecFieldDescriptor[] = [
  { name: "source", label: "Source", widget: "text", readOnly: true },
  {
    name: "target",
    label: "Target channel",
    widget: "many2one",
    required: true,
    relation: {
      resource: "messaging.Channel",
      labelField: "name",
      create: { resource: "messaging.Channel" },
    },
  },
  { name: "approved", label: "Approved", widget: "switch" },
];

const channels = [
  { id: "chn-support", name: "Customer support" },
  { id: "chn-research", name: "Product research" },
];

const channelMetadata = {
  angee: {
    resources: [
      {
        schemaName: "public",
        modelLabel: "messaging.Channel",
        appLabel: "messaging",
        modelName: "Channel",
        publicIdField: "id",
        roots: {
          list: "channels",
          create: "insert_channels_one",
        },
        typeNames: { node: "ChannelType" },
        recordRepresentation: "name",
        capabilities: ["list", "create"],
        fields: [
          {
            name: "id",
            kind: "scalar",
            scalar: "ID",
            readable: true,
            filterable: true,
            sortable: true,
            aggregatable: false,
            groupable: true,
            creatable: false,
            updatable: false,
            requiredOnCreate: false,
          },
          {
            name: "name",
            kind: "scalar",
            scalar: "String",
            readable: true,
            filterable: true,
            sortable: true,
            aggregatable: false,
            groupable: true,
            creatable: true,
            updatable: true,
            requiredOnCreate: true,
          },
        ],
        filterFields: [],
        orderFields: ["name"],
        aggregateFields: [],
        groupByFields: [],
        relationAxes: [],
      },
    ],
  },
} satisfies AngeeSchemaMetadata;

const storySchemas = storySchema(async () =>
  jsonResponse({
    data: {
      channels,
      channels_aggregate: { aggregate: { count: channels.length } },
      insert_channels_one: { id: "chn-new", name: "New channel" },
    },
  }),
);
storySchemas.public = { ...storySchemas.public!, metadata: channelMetadata };

const meta = {
  title: "Widgets/Rows",
  parameters: { layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

function RowsDemo(): ReactElement {
  const [rows, setRows] = useState<RowsValue>([
    { source: "WhatsApp archive", target: "chn-support", approved: true },
    { source: "Telegram takeout", target: "chn-research", approved: false },
  ]);
  const field = {
    name: "rows",
    label: "Mappings",
    kind: "array",
    widget: "rows",
    rowTemplate,
  } satisfies FormSpecFieldDescriptor;
  return (
    <RuntimeFixture schemas={storySchemas}>
      <div className="grid max-w-5xl gap-8">
        <section className="grid gap-2">
          <h3 className="text-13 font-semibold text-fg">Edit</h3>
          <LabeledDescriptorField
            field={field}
            value={rows}
            onChange={(value) => setRows(value as RowsValue)}
          />
        </section>
        <section className="grid gap-2">
          <h3 className="text-13 font-semibold text-fg">Read</h3>
          <LabeledDescriptorField
            field={field}
            value={rows}
            readOnly
            onChange={() => undefined}
          />
        </section>
      </div>
    </RuntimeFixture>
  );
}

export const EditAndRead: Story = {
  render: () => <RowsDemo />,
};
