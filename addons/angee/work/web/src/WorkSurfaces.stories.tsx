import { StatusbarSteps, type StatusbarStep } from "@angee/ui";

import { TriageDwell } from "./task-work";

const queueStages: readonly StatusbarStep[] = [
  { value: "triage", label: "Triage" },
  { value: "ready", label: "Ready" },
  { value: "active", label: "Active" },
  { value: "done", label: "Done" },
  { value: "duplicate", label: "Duplicate", onPath: false, date: "2026-09-24" },
];

const meta = {
  title: "Work/Task surfaces",
  parameters: { layout: "centered" },
};

export default meta;

export const DwellTime = {
  render: () => <TriageDwell value="2026-08-22T08:30:00Z" />,
};

export const DuplicateStage = {
  render: () => <div className="w-[650px]"><StatusbarSteps aria-label="Stage" steps={queueStages} value="duplicate" fill readOnly /></div>,
};
