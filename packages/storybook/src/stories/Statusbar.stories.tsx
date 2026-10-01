import type { Meta, StoryObj } from "@storybook/react-vite";
import { StatusbarSkeleton, StatusbarSteps, type StatusbarStep } from "@angee/ui";

const steps: readonly StatusbarStep[] = [
  { value: "new", label: "New", onPath: true, startDate: "2026-09-22", endDate: "2026-10-01" },
  { value: "planned", label: "Planned", onPath: true, startDate: "2026-10-02", endDate: "2026-10-21" },
  { value: "active", label: "Active", onPath: true, startDate: "2026-10-24", endDate: "2026-11-18", note: "Ends with: approval" },
  { value: "review", label: "Review", onPath: true },
  { value: "done", label: "Done", onPath: true },
  { value: "withdrawn", label: "Withdrawn", onPath: false, date: "2026-09-24" },
];

const meta = { title: "Widgets/Statusbar", component: StatusbarSteps, parameters: { layout: "padded" } } satisfies Meta<typeof StatusbarSteps>;
export default meta;
type Story = StoryObj;

export const CompletedCurrentUpcoming: Story = {
  render: () => <div className="w-[850px]"><StatusbarSteps aria-label="Progress" steps={steps} value="active" fill /></div>,
};

export const OffPath: Story = {
  render: () => <div className="w-[850px]"><StatusbarSteps aria-label="Progress" steps={steps} value="withdrawn" fill /></div>,
};

export const PausedLifecycle: Story = {
  render: () => <div className="w-[850px]"><StatusbarSteps aria-label="Progress" steps={steps} value="active" fill
    offPath={{ label: "Paused", date: "2026-10-29" }} /></div>,
};

export const CompletedLifecycle: Story = {
  render: () => <div className="w-[850px]"><StatusbarSteps aria-label="Progress" steps={steps} value="done" fill
    offPath={{ label: "Completed", date: "2026-11-20" }} /></div>,
};

export const DroppedLifecycle: Story = {
  render: () => <div className="w-[850px]"><StatusbarSteps aria-label="Progress" steps={steps} value="active" fill
    offPath={{ label: "Dropped", date: "2026-10-30" }} /></div>,
};

export const Narrow: Story = {
  render: () => <div className="w-48"><StatusbarSteps aria-label="Progress" steps={steps} value="active" containerWidth={192} /></div>,
};

export const Loading: Story = {
  render: () => <div className="w-[850px]"><StatusbarSkeleton count={5} fill twoLine /></div>,
};

export const LoadingOneLine: Story = {
  render: () => <StatusbarSkeleton count={4} />,
};
