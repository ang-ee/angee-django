import { decisionFixture } from "@angee/decisions/testing";
import type { TimelineData, TimelineSelection } from "./RecordTimeline";

export type TimelineState = "decision" | "clean" | "error" | "run" | "stopped" | "set" | "empty" | "unknown";
type Run = TimelineData[number]["runs"][number];
type Node = Run["graph"]["nodes"][number];
const at = "2026-10-03T10:00:00Z";

/** Neutral fixtures over the generated read, including long history and branches. */
export function timelineFixture(state: TimelineState = "decision"): TimelineSelection {
  const decision = decisionFixture({ kind_label: "Confirm the name", proposal: {
    alternatives: [{ key: "accept", label: "Use proposed name", outcome: "accepted",
      actions: { nte_7: { fields: { display_name: { set: "Reviewed notes" } } } } },
      { key: "keep", label: "Keep what is on the record", outcome: "kept" }],
  } });
  const node = (key: string, label: string, rank: number, plan: string): Node => ({
    key, label, rank, plan, body_key: null, step_label: label, outcomes: [], item_counts: [], item_attempts: 0,
    step_run: plan === "certain" || plan === "optional" ? null : {
      id: `wsr_${key}`, status: plan === "done" ? "SUCCEEDED" : "WAITING", hold: plan === "done" ? null : "decision",
      outcome: plan === "done" ? "done" : "", outcome_label: plan === "done" ? "Done" : "", failure_reason: null,
      wait_reason: "", notes: [], attempt: 1, page_index: 0, map_total: 0, map_settled: 0,
      created_at: at, updated_at: at, can_retry: false, requires_duplicate_acknowledgement: false,
      awaited_run: null, child_runs: [], records: [], decision: null, map_steps: [],
    },
  });
  const nodes = Array.from({ length: 7 }, (_, index) => node(`read${index}`, ["Receive record", "Read source", "Identify record", "Match owner", "Read context", "Check links", "Prepare choices"][index]!, index, "done"));
  nodes.push(node("review", "Review the record", 7, "current"), node("finish", "Finish the plan", 8, "certain"), node("extra", "Check a related record", 9, "optional"));
  const review = nodes[7]!.step_run!;
  review.decision = decision;
  review.records = [{ id: "wsrec_7", label: "Review notes", operation: "read", record_model: "notes.Note", record_id: "nte_7" }];
  const run: Run = {
    id: "wfr_review", display_name: "Record review", status: "WAITING", origin: "MANUAL", outcome_label: "", failure_reason: null,
    version: { workflow: { display_name: "Record review" } },
    created_at: at, finished_at: null, stopped_at: null, output: {}, can_cancel: true, run_as: { display_name: "River" },
    subject_model: null, subject_id: null,
    parent_step: null, trigger_event: null, graph: { nodes, edges: [] },
  };
  if (state === "clean") {
    run.status = "SUCCEEDED"; run.can_cancel = false; run.finished_at = at;
    decision.is_open = false; decision.verdict = ["accept"]; decision.answered_by = { display_name: "River" }; decision.answered_at = at;
    decision.verdict_label = "Use proposed name";
    review.outcome = "accepted"; review.outcome_label = "Use proposed name";
    for (const node of nodes) if (node.plan !== "optional") { node.plan = "done"; if (node.step_run) { node.step_run.status = "SUCCEEDED"; node.step_run.hold = null; } }
    nodes[8] = node("finish", "Finish the plan", 8, "done"); nodes[9]!.plan = "not_run";
  } else if (state === "error" || state === "run") {
    review.decision = null; review.hold = state;
    if (state === "error") { review.status = "FAILED"; review.failure_reason = "The operation could not finish."; review.can_retry = true; run.status = "FAILED"; run.failure_reason = "A parallel branch failed."; }
    else review.awaited_run = { id: "wfr_child", display_name: "Related record check", status: "WAITING" };
  } else if (state === "stopped") {
    run.status = "CANCELED"; run.can_cancel = false; run.finished_at = at; run.stopped_at = at;
    review.status = "CANCELED"; review.hold = null; nodes[7]!.plan = "done"; nodes[8]!.plan = nodes[9]!.plan = "not_run";
    decision.is_open = false; decision.verdict = []; decision.answered_by = { display_name: "River" }; decision.answered_at = at;
  }
  // A status the client does not know: the generated enums cannot name it, so the fixture widens the type.
  if (state === "unknown") { (run as { status: string }).status = "FUTURE_STATE"; (review as { status: string }).status = "FUTURE_STATE"; }
  const entry: TimelineData[number] = {
    record_model: "notes.Note", record_id: "nte_7",
    decisions: decision.is_open && state !== "error" && state !== "run" ? [decision] : [], runs: [run],
  };
  if (state === "empty") { entry.runs = []; entry.decisions = []; }
  if (state === "set") {
    const shared = entry.decisions[0]!;
    shared.records.push({ ...shared.records[0]!, id: "dcr_other", record_id: "nte_8" });
  }
  const records = state === "set" ? [entry, {
    ...entry, record_id: "nte_8",
    runs: timelineFixture("error").records[0]!.runs.map((run) => ({ ...run, id: "wfr_error" })),
  }] : [entry];
  return { records, open_decision_count: entry.decisions.length };
}
