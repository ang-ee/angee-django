import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Badge,
  Page,
  PageBody,
  PageHeader,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@angee/base";

const meta = {
  title: "Page/PageBody",
  component: PageBody,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof PageBody>;

export default meta;

type Story = StoryObj<typeof meta>;

export const ScrollRegion: Story = {
  render: () => (
    <Page className="h-[28rem] w-[52rem] overflow-hidden rounded-md border border-border-subtle">
      <PageHeader density="compact" title="Activity" />
      <PageBody gutter="none">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead sticky>Event</TableHead>
              <TableHead sticky>Owner</TableHead>
              <TableHead sticky>State</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {Array.from({ length: 24 }, (_, index) => (
              <TableRow key={index} interactive>
                <TableCell>Record update {index + 1}</TableCell>
                <TableCell className="text-fg-muted">System</TableCell>
                <TableCell>
                  <Badge tone={index % 3 === 0 ? "success" : "neutral"}>
                    {index % 3 === 0 ? "Complete" : "Queued"}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </PageBody>
    </Page>
  ),
};
