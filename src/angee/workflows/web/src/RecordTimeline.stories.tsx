import { useMemo } from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import { decisionLinkFixture, decisionRecordFixture, decisionResourceFixture, decisionUserFixture } from "@angee/decisions/testing";
import { storyAppearance, type RuntimeFixtureProps, RoutedRuntimeFixture, storySchema, type StoryRequest } from "@angee/storybook/testing";
import {
  Button, ChatterProvider, FormView, RecordFieldMarksProvider, ScrollArea, createRouteHref, useChatter,
} from "@angee/ui";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { RecordTimeline, type TimelineSelection } from "./RecordTimeline";
import { useRecordTimelineAttention } from "./timeline-pane";
import { timelineFixture, type TimelineState } from "./timeline-testing";
import { WORKFLOW_STATUS_TONES } from "./status-tones";
import { attemptResourceFixture, recordResourceFixture, runResourceFixture, stepRunResourceFixture, watchResourceFixture } from "./testing";

export default { title: "Workflows/Record timeline", parameters: { angeeOwnRuntime: true, layout: "fullscreen" }, excludeStories: ["TimelineStory"] };
export const WaitingOnDecision = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TimelineStory appearance={storyAppearance(globals)} /> };
export const Clean = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TimelineStory appearance={storyAppearance(globals)} state="clean" /> };
export const HeldByError = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TimelineStory appearance={storyAppearance(globals)} state="error" /> };
export const RetryWithAcknowledgement = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TimelineStory appearance={storyAppearance(globals)} state="error" duplicateRisk /> };
export const HeldByRun = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TimelineStory appearance={storyAppearance(globals)} state="run" /> };
export const Stopped = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TimelineStory appearance={storyAppearance(globals)} state="stopped" /> };
export const RecordSet = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <TimelineStory appearance={storyAppearance(globals)} state="set" /> };

const resource = testDataResource("notes.Note", {
  ...decisionRecordFixture, fields: ["display_name", "body"].map((name) => ({ name, label: name === "display_name" ? "Name" : "Body", kind: "scalar", scalar: "String", readable: true, creatable: true, updatable: true, aggregatable: false, requiredOnCreate: false })),
  roots: { detail: "notes_by_pk", list: "notes" },
  query: testResourceQuery({ fields: { id: testQueryField("id"), display_name: testQueryField("display_name"), body: testQueryField("body") } }),
});
const runtime = {
  statusTones: WORKFLOW_STATUS_TONES,
  routeHref: createRouteHref([{ name: "notes.record", path: "/notes/$id" }, { name: "workflows.runs.record", path: "/workflows/runs/$id" }]),
  routesByResource: { "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } }, "workflows.WorkflowRun": { collection: "workflows.runs", record: { name: "workflows.runs.record", param: "id" } } },
};

/** The real read and inline mutation owners over a neutral, disposable transport. */
export function TimelineStory({ appearance, state = "decision", collapsed = false, duplicateRisk = false, data, onRequest }: {
  appearance?: RuntimeFixtureProps["appearance"];
  state?: TimelineState; collapsed?: boolean; duplicateRisk?: boolean; onRequest?: (request: StoryRequest) => void;
  data?: TimelineSelection;
}) {
  const schemas = useMemo(() => {
    let current = data ?? timelineFixture(state);
    if (duplicateRisk) current.records[0]!.runs[0]!.graph_step_runs.find((step) => step.id === "wsr_review")!.requires_duplicate_acknowledgement = true;
    const retry = () => {
      current = timelineFixture("decision");
      return { ok: true, id: "wsr_review", message: "Retry requested.", validation_errors: {} };
    };
    const fixture = storySchema({
      decide: () => { current = timelineFixture("clean"); return { ok: true, id: "dcn_review", message: "Answer recorded.", validation_errors: {} }; },
      cancel_workflow_run: () => { current = timelineFixture("stopped"); return { ok: true, id: "wfr_review", message: "Run stopped." }; },
      retry_step: retry,
      retry_step_accepting_duplicate: retry,
      record_timeline: () => ({ ...current, has_runs: current.records.some((entry) => entry.runs.length > 0) }),
      user_by_pk: () => ({ id: "usr_river", display_name: "River" }),
      notes_by_pk: () => ({ id: "nte_7", revision: 1, display_name: current.open_decision_count ? "Review notes" : "Reviewed notes", body: "A record to check.", permissions: ["write"] }),
    }, onRequest).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [resource, decisionResourceFixture, decisionLinkFixture, decisionUserFixture, runResourceFixture, stepRunResourceFixture, attemptResourceFixture, recordResourceFixture, watchResourceFixture] } } } };
  }, [state, duplicateRisk, data, onRequest]);
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={schemas} initialEntry="/notes/nte_7" collectionPath="/notes" resourceLabel="Notes" runtime={runtime} operationDocuments={{ console: operationDocuments }}>
    <RecordFieldMarksProvider><ChatterProvider defaultCollapsed={collapsed}><TimelineLayout set={state === "set"} /></ChatterProvider></RecordFieldMarksProvider>
  </RoutedRuntimeFixture>;
}

/** The run body as the aside's drill-in renders it, beside the form it belongs to. */
function TimelineLayout({ set }: { set: boolean }) {
  const record = useMemo(() => set ? [{ model: "notes.Note", id: "nte_7" }, { model: "notes.Note", id: "nte_8" }] : { model: "notes.Note", id: "nte_7" }, [set]);
  useRecordTimelineAttention(record);
  const chatter = useChatter();
  return <div className="grid min-h-0 w-full bg-sheet" style={{ gridTemplateColumns: "minmax(0,1fr) 24rem", height: "100vh" }}>
    <main className="min-w-0 p-6"><FormView resource="notes.Note" id="nte_7" fields={[{ name: "display_name", label: "Name" }, { name: "body", label: "Body" }]} />
      <Button onClick={() => chatter.setCollapsed(!chatter.collapsed)}>Toggle timeline</Button>
    </main>
    <aside data-testid="timeline-aside" data-state={`${chatter.collapsed}:${chatter.activeTab}`} className="min-h-0 border-l border-border-subtle">
      <ScrollArea className="h-full" viewportClassName="p-4"><RecordTimeline record={record} /></ScrollArea>
    </aside>
  </div>;
}
