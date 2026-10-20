import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { createElement } from "react";
import { Redo2, Workflow } from "lucide-react";
import { lazyRouteComponent } from "@tanstack/react-router";
import { RUN_MODEL } from "./documents.console";
import { WORKFLOW_MODEL } from "./catalogue/resources";
import { decisionRunOrigin } from "./contributions";
import { decisionRunGroup, recordRunsGroup, runDecisionsGroup, runRecordsGroup } from "./related";
import { enWorkflowsMessages } from "./i18n";
import { TriggerCondition } from "./TriggerCondition";
import { TRIGGER_MODEL, TRIGGER_EVENT_MODEL } from "./triggers";
import { WORKFLOW_STATUS_TONES } from "./status-tones";
import { RunWorkflowRecordActions } from "./run-workflow-actions";
import { runStatusWidget } from "./run-status";
import { RecordTimeline } from "./RecordTimeline";

export { RecordTimeline, type RecordTimelineProps, type TimelineRecord } from "./RecordTimeline";
export { useRecordTimelineAttention } from "./timeline-pane";
export { useRecordTimelineQuery } from "./RecordTimeline";
export { WORKFLOW_STUDIO_TAB_ID } from "./catalogue/resources";
export { runStatusColumn } from "./run-status";

export { TRIGGER_MODEL, TRIGGER_EVENT_MODEL } from "./triggers";

export default defineBaseAddon({
  id: "workflows",
  routes: [
    ...resourcePageRoutes("workflows.runs", "/workflows/runs", lazyRouteComponent(() => import("./RunsPage"), "RunsPage"), RUN_MODEL, { menu: "workflows.runs" }),
    ...resourcePageRoutes("workflows.catalogue", "/workflows", lazyRouteComponent(() => import("./WorkflowsPage"), "WorkflowsPage"), WORKFLOW_MODEL),
    ...resourcePageRoutes("workflows.triggers", "/workflows/triggers", lazyRouteComponent(() => import("./TriggersPage"), "TriggersPage"), TRIGGER_MODEL),
    ...resourcePageRoutes("workflows.trigger-events", "/workflows/trigger-events", lazyRouteComponent(() => import("./TriggerEventsPage"), "TriggerEventsPage"), TRIGGER_EVENT_MODEL),
  ],
  menus: {
    // Decisions is workflows' human-review inbox; it lives under Workflows.
    workflows: { label: "Workflows", icon: "versions", include: [{ id: "decisions", flatten: true }] },
    "workflows.runs": { parent: "workflows", label: "Runs", icon: "activity", route: "workflows.runs", sequence: 10 },
    "workflows.studio": { parent: "workflows", label: "Studio", icon: "versions", sequence: 30 },
    "workflows.catalogue": { parent: "workflows.studio", label: "Workflows", icon: "versions", route: "workflows.catalogue" },
    "workflows.triggers": { parent: "workflows.studio", label: "Triggers", icon: "activity", route: "workflows.triggers" },
  },
  containers: {
    "form#actions-menu": { "workflows.run-workflow": { sequence: 40, content: createElement(RunWorkflowRecordActions) } },
    // Runs on the Timeline: one line per run, opening the run drill-in.
    "timeline#kinds": {
      "workflows.run": { content: { label: "Runs", icon: "workflow", tone: "info", verb: "Open run", target: { tab: "workflows.run", focus: "decision" } } },
    },
    "record#aside": {
      "workflows.run": { sequence: 12, content: {
        label: "Run", icon: "workflow", panelClassName: "p-0", strip: false, target: { model: RUN_MODEL },
        render: (context, selection) => createElement(RecordTimeline, {
          record: { model: context.route?.canonicalLabel ?? context.route?.modelLabel ?? "", id: context.view.kind === "record" ? context.view.sqid ?? "" : "" },
          runId: selection?.target,
        }),
      } },
    },
    "decisions#origin": { "workflows.run": decisionRunOrigin },
    // Related: a run lists the records it worked on and the decisions it asked; every other
    // record lists the runs that worked on it; a decision lists the run that asked it.
    "related#groups": { "workflows.runs": recordRunsGroup },
    "workflows.WorkflowRun#groups": { "workflows.records": runRecordsGroup, "workflows.decisions": runDecisionsGroup },
    "decisions.Decision#groups": { "workflows.run": decisionRunGroup },
  },
  statusTones: WORKFLOW_STATUS_TONES,
  icons: { workflow: Workflow, "redo-2": Redo2 },

  widgets: {
    "angee.workflows.condition": { read: TriggerCondition, edit: TriggerCondition },
    runStatus: runStatusWidget,
  },
  i18n: { workflows: enWorkflowsMessages },
});
