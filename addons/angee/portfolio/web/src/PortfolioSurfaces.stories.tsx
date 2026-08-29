import { PortfolioHealthSummary } from "./update-composer";

const meta = {
  title: "Portfolio/Health reporting",
  parameters: { layout: "centered" },
};

export default meta;

export const OnTrack = {
  render: () => (
    <div className="w-80 rounded-6 border border-border-subtle bg-sheet p-4">
      <PortfolioHealthSummary
        health="ON_TRACK"
        updatedAt="2026-08-22T08:30:00Z"
      />
    </div>
  ),
};

export const AtRisk = {
  render: () => (
    <div className="w-80 rounded-6 border border-border-subtle bg-sheet p-4">
      <PortfolioHealthSummary
        health="AT_RISK"
        updatedAt="2026-08-21T16:15:00Z"
      />
    </div>
  ),
};

export const AwaitingFirstReport = {
  render: () => (
    <div className="w-80 rounded-6 border border-border-subtle bg-sheet p-4">
      <PortfolioHealthSummary health={null} updatedAt={null} />
    </div>
  ),
};
