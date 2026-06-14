import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Badge,
  FieldRow,
  Input,
  SplitView,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  STATUS_BADGE_TONES,
  stateToneFromValue,
} from "@angee/base";

const records = [
  ["Q3 review brief", "Active"],
  ["Clinic rollout notes", "Draft"],
  ["Access audit outline", "Active"],
  ["Storage policy memo", "Archived"],
] as const;

const meta = {
  title: "Layouts/SplitView",
  component: SplitView,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof SplitView>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ListDetail: Story = {
  render: () => (
    <div className="h-screen bg-inset p-6">
      <div className="h-full overflow-hidden rounded-md border border-border-subtle bg-canvas">
        <SplitView primarySize={36}>
          <SplitView.Primary>
            <div className="h-full overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead sticky>Title</TableHead>
                    <TableHead sticky>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {records.map(([title, status]) => (
                    <TableRow key={title} interactive>
                      <TableCell className="font-medium">{title}</TableCell>
                      <TableCell>
                        <Badge tone={stateToneFromValue(status, STATUS_BADGE_TONES)}>{status}</Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </SplitView.Primary>
          <SplitView.Detail>
            <div className="space-y-4 overflow-auto p-5">
              <h2 className="text-15 font-semibold text-fg">Q3 review brief</h2>
              <FieldRow label="Title">
                <Input defaultValue="Q3 review brief" />
              </FieldRow>
              <FieldRow label="Owner">
                <Input defaultValue="Sofia" />
              </FieldRow>
            </div>
          </SplitView.Detail>
        </SplitView>
      </div>
    </div>
  ),
};
