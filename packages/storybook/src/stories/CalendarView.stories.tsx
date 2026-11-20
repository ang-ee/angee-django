import type { Meta, StoryObj } from "@storybook/react-vite";
import { CalendarView, type Occurrence } from "@angee/ui";

const RANGE = { start: new Date(2026, 8, 1), end: new Date(2026, 8, 1) };

const OCCURRENCES: readonly Occurrence[] = [
  {
    occurrence_id: "evt_reading",
    event_sqid: "evt_reading",
    title: "Evening reading circle",
    start: "2026-09-10T18:00:00.000Z",
    end: "2026-09-10T20:00:00.000Z",
    all_day: false,
    editable: true,
  },
  {
    occurrence_id: "evt_workshop:20260917T090000Z",
    event_sqid: "evt_workshop",
    title: "Printing workshop",
    start: "2026-09-17T09:00:00.000Z",
    end: "2026-09-17T12:00:00.000Z",
    all_day: false,
    editable: false,
  },
  {
    occurrence_id: "task:proofs",
    event_sqid: "",
    title: "Proofs due",
    start: "2026-09-21",
    end: "2026-09-21",
    all_day: true,
    editable: false,
  },
];

const meta = {
  title: "Views/CalendarView",
  component: CalendarView,
  parameters: { layout: "padded" },
  args: { occurrences: OCCURRENCES, range: RANGE, view: "month" },
} satisfies Meta<typeof CalendarView>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** The list mode: one month's occurrences under their day headers. */
export const List: Story = { args: { view: "list" } };
