import { TriageDwell, WorkTaskCard } from "./task-work";

const meta = {
  title: "Work/Task surfaces",
  parameters: { layout: "centered" },
};

export default meta;

export const QueueCard = {
  render: () => (
    <div className="w-72 rounded-6 border border-border-subtle bg-sheet p-4">
      <WorkTaskCard
        task={{ id: "task-42", title: "Ship the queue board", work_key: "ENG-42", estimate: 5 }}
        estimateScale="FIBONACCI"
      />
    </div>
  ),
};

export const TShirtCard = {
  render: () => (
    <div className="w-72 rounded-6 border border-border-subtle bg-sheet p-4">
      <WorkTaskCard
        task={{ id: "task-43", title: "Triage an incoming request", work_key: "ENG-43", estimate: 8 }}
        estimateScale="TSHIRT"
      />
    </div>
  ),
};

export const DwellTime = {
  render: () => <TriageDwell value="2026-08-22T08:30:00Z" />,
};
