import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  DataLens,
  type DataLensProjection,
  type DataLensVisual,
} from "@angee/base";

const rows = [
  { id: "api", name: "Production API", value: 42, tone: "success" },
  { id: "csv", name: "CSV import", value: 18, tone: "info" },
  { id: "mail", name: "Mailbox sync", value: 9, tone: "warning" },
  { id: "files", name: "File drop", value: 27, tone: "brand" },
] as const;

const visuals = ["graph", "chart", "metrics", "map", "tree"] satisfies readonly DataLensVisual[];

const meta = {
  title: "Fragments/DataLens",
  component: DataLens,
  parameters: { layout: "padded" },
} satisfies Meta<typeof DataLens>;

export default meta;

type Story = StoryObj;

export const Lens: Story = {
  render: () => (
    <div className="grid max-w-5xl gap-4 md:grid-cols-2">
      {visuals.map((visual) => (
        <DataLens
          key={visual}
          project={(items): DataLensProjection => ({
            metrics: [
              { label: "Rows", value: items.length.toLocaleString() },
              {
                label: "Total",
                value: items.reduce((sum, item) => sum + item.value, 0).toLocaleString(),
              },
            ],
            nodes: items.map((item) => ({
              id: item.id,
              label: item.name,
              value: item.value,
              tone: item.tone,
            })),
            summary: items.length,
            title: "Source lens",
          })}
          rows={rows}
          visual={visual}
        />
      ))}
    </div>
  ),
};
