import type { Meta, StoryObj } from "@storybook/react-vite";
import { AgentCard, type AgentStatus } from "@angee/ui";
import { Button, Glyph } from "@angee/ui";

const meta = {
  title: "Fragments/AgentCard",
  component: AgentCard,
  parameters: {
    layout: "padded",
  },
  argTypes: {
    status: {
      control: "select",
      options: ["running", "idle", "success", "error", "paused"] satisfies AgentStatus[],
    },
    progress: {
      control: { type: "range", min: 0, max: 100, step: 1 },
    },
  },
  args: {
    name: "Research Agent",
    model: "claude-sonnet-4",
    icon: "cpu",
    status: "running",
    statusMessage: "Scanning 47 documents for relevance…",
    progress: 62,
  },
} satisfies Meta<typeof AgentCard>;

export default meta;

type Story = StoryObj<typeof meta>;

// ─── Playground ───────────────────────────────────────────────────────────────

export const Playground: Story = {
  args: {
    startedAt: new Date(Date.now() - 1000 * 93),
    actions: [
      {
        label: "Pause",
        icon: "pause",
        onClick: () => undefined,
      },
      {
        label: "Stop",
        icon: "square",
        variant: "danger",
        onClick: () => undefined,
      },
    ],
  },
};

// ─── All statuses ─────────────────────────────────────────────────────────────

export const AllStatuses: Story = {
  name: "All statuses",
  render: () => (
    <div className="grid max-w-2xl gap-3 sm:grid-cols-2">
      <AgentCard
        name="Research Agent"
        model="claude-sonnet-4"
        icon="cpu"
        status="running"
        statusMessage="Scanning 47 documents for relevance…"
        progress={62}
        startedAt={new Date(Date.now() - 1000 * 93)}
        actions={[
          { label: "Pause", icon: "pause", onClick: () => undefined },
          { label: "Stop", icon: "square", variant: "danger", onClick: () => undefined },
        ]}
      />
      <AgentCard
        name="Build Agent"
        model="codex"
        icon="hammer"
        status="success"
        statusMessage="All 12 files compiled without errors."
        progress={100}
        startedAt={new Date(Date.now() - 1000 * 240)}
        actions={[
          { label: "View output", icon: "external-link", onClick: () => undefined },
        ]}
      />
      <AgentCard
        name="Ingest Pipeline"
        model="gpt-4o"
        icon="database"
        status="error"
        statusMessage="Connection to vector store timed out."
        startedAt={new Date(Date.now() - 1000 * 18)}
        actions={[
          { label: "Retry", icon: "rotate-ccw", onClick: () => undefined },
          { label: "Logs", icon: "scroll-text", onClick: () => undefined },
        ]}
      />
      <AgentCard
        name="Summarizer"
        model="claude-haiku-3"
        icon="file-text"
        status="paused"
        statusMessage="Waiting for rate-limit window to reset."
        progress={38}
        startedAt={new Date(Date.now() - 1000 * 420)}
        actions={[
          { label: "Resume", icon: "play", onClick: () => undefined },
          { label: "Cancel", icon: "x", variant: "danger", onClick: () => undefined },
        ]}
      />
      <AgentCard
        name="Classifier"
        model="claude-haiku-3"
        icon="tag"
        status="idle"
      />
    </div>
  ),
};

// ─── With log output ──────────────────────────────────────────────────────────

export const WithLogOutput: Story = {
  name: "With log output",
  render: () => (
    <div className="max-w-xl">
      <AgentCard
        name="Research Agent"
        model="claude-sonnet-4"
        icon="cpu"
        status="running"
        statusMessage="Cross-referencing 12 sources…"
        progress={71}
        startedAt={new Date(Date.now() - 1000 * 151)}
        actions={[
          { label: "Pause", icon: "pause", onClick: () => undefined },
        ]}
      >
        <div className="rounded-6 bg-inset px-3 py-2.5 font-mono text-2xs text-fg-muted space-y-0.5">
          <p className="text-success-text">[ok] Loaded 47 documents</p>
          <p>[..] Embedding batch 3 / 6</p>
          <p>[..] Ranking by semantic similarity</p>
          <p className="text-fg-subtle">[..] Cross-referencing sources…</p>
        </div>
      </AgentCard>
    </div>
  ),
};

// ─── Minimal (no actions, no progress) ───────────────────────────────────────

export const Minimal: Story = {
  name: "Minimal",
  render: () => (
    <div className="flex max-w-sm flex-col gap-3">
      <AgentCard name="Watcher" status="idle" />
      <AgentCard
        name="Notifier"
        model="claude-haiku-3"
        status="running"
        statusMessage="Listening for webhook events…"
      />
    </div>
  ),
};

// ─── Grid layout ─────────────────────────────────────────────────────────────

export const GridLayout: Story = {
  name: "Grid layout (ARPEE dashboard)",
  parameters: { layout: "fullscreen" },
  render: () => (
    <div className="min-h-screen bg-canvas p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-22 font-semibold text-fg">Active agents</h1>
          <p className="mt-0.5 text-13 text-fg-muted">
            6 agents running across 3 workspaces
          </p>
        </div>
        <Button variant="primary" size="md">
          <Glyph name="plus" size={14} />
          New agent
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <AgentCard
          name="Research Agent"
          model="claude-sonnet-4"
          icon="cpu"
          status="running"
          statusMessage="Scanning 47 documents for relevance…"
          progress={62}
          startedAt={new Date(Date.now() - 1000 * 93)}
          actions={[
            { label: "Pause", icon: "pause", onClick: () => undefined },
            { label: "Stop", icon: "square", variant: "danger", onClick: () => undefined },
          ]}
        />
        <AgentCard
          name="Content Writer"
          model="claude-sonnet-4"
          icon="pen-line"
          status="running"
          statusMessage="Writing section 3 of 8: Methodology…"
          progress={34}
          startedAt={new Date(Date.now() - 1000 * 47)}
          actions={[
            { label: "Pause", icon: "pause", onClick: () => undefined },
          ]}
        />
        <AgentCard
          name="Data Ingestor"
          model="gpt-4o"
          icon="database"
          status="running"
          statusMessage="Processing batch 14 / 20…"
          progress={70}
          startedAt={new Date(Date.now() - 1000 * 312)}
          actions={[
            { label: "Pause", icon: "pause", onClick: () => undefined },
          ]}
        />
        <AgentCard
          name="Summarizer"
          model="claude-haiku-3"
          icon="file-text"
          status="paused"
          statusMessage="Waiting for rate-limit window."
          progress={38}
          startedAt={new Date(Date.now() - 1000 * 420)}
          actions={[
            { label: "Resume", icon: "play", onClick: () => undefined },
            { label: "Cancel", icon: "x", variant: "danger", onClick: () => undefined },
          ]}
        />
        <AgentCard
          name="Build Agent"
          model="codex"
          icon="hammer"
          status="success"
          statusMessage="All 12 files compiled without errors."
          progress={100}
          startedAt={new Date(Date.now() - 1000 * 240)}
          actions={[
            { label: "View output", icon: "external-link", onClick: () => undefined },
          ]}
        />
        <AgentCard
          name="Ingest Pipeline"
          model="gpt-4o"
          icon="server"
          status="error"
          statusMessage="Connection to vector store timed out."
          startedAt={new Date(Date.now() - 1000 * 18)}
          actions={[
            { label: "Retry", icon: "rotate-ccw", onClick: () => undefined },
            { label: "Logs", icon: "scroll-text", onClick: () => undefined },
          ]}
        />
      </div>
    </div>
  ),
};
