import type { Meta, StoryObj } from "@storybook/react-vite";
import { GanttLane, GanttView } from "@angee/ui";
import { toneColorVar } from "@angee/ui/lib/tones";
import { Badge } from "@angee/ui/ui/badge";

const anchor = new Date("2026-09-14T12:00:00Z");
const meta = {
  title: "Views/GanttView",
  component: GanttView,
  parameters: { layout: "fullscreen" },
  args: {
    defaultDate: anchor,
    defaultScale: "month",
    nowIndicator: true,
    resources: [
      { id: "north", title: "North team" },
      { id: "south", title: "South team" },
      { id: "west", title: "West team" },
    ],
    events: [
      { id: "one", title: "Review", resourceId: "north", start: new Date("2026-09-08T00:00:00Z"), end: new Date("2026-09-18T00:00:00Z"), color: toneColorVar("brand") },
      { id: "two", title: "Delivery", resourceId: "north", start: new Date("2026-09-12T00:00:00Z"), end: new Date("2026-09-22T00:00:00Z"), color: toneColorVar("brand"), current: true, note: "Next: handoff" },
      { id: "three", title: "Planning", resourceId: "south", start: new Date("2026-09-10T00:00:00Z"), end: new Date("2026-09-16T00:00:00Z"), color: toneColorVar("warning") },
    ],
    renderRowContent: (row) => row.id === "west" ? <div className="flex min-w-0 items-center gap-2">
      <GanttLane details={{ title: row.title, href: `/teams/${row.id}` }} />
      <Badge tone="neutral">Available</Badge>
    </div> : <GanttLane details={{ title: row.title, href: `/teams/${row.id}`, secondary: "Current phase: delivery",
      people: [{ id: "ada", name: "Ada Lovelace" }, { id: "grace", name: "Grace Hopper" }] }} />,
  },
  decorators: [(Story) => <div className="h-[600px] bg-sheet"><Story /></div>],
} satisfies Meta<typeof GanttView>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Light: Story = { globals: { colorScheme: "light" } };
export const Dark: Story = { globals: { colorScheme: "dark" } };
