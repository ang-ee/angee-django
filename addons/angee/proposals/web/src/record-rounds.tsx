import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import {
  Glyph,
  ListView,
  Tab,
  formViewSectionsSlot,
  formViewRecordActionsSlot,
  useRecordChromeContext,
  useRouteHref,
  type ListColumn,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { enProposalsMessages, useProposalsT } from "./i18n";
import { ProjectRoundRecord, RoundRecordSection } from "./round-record";
import { ROUND_MODEL } from "./resources";
import { TaskResponderShareAction } from "./task-responder-share";

const ROUND_RECORD_FIELDS = ["id", "name", "status", "opening_policy", "permissions", "revision", "can_open", "can_admit",
  "roster.user", "roster.name", "roster.track_status"] as const;
const PROJECT_ROUND_FIELDS = ROUND_RECORD_FIELDS.map((field) => `active_proposal_round.${field}`);

interface RoundPaneRow extends StringIdRow {
  name?: unknown;
  status?: unknown;
  last_call_at?: unknown;
  submission_deadline?: unknown;
  facilitator?: unknown;
}


function RecordRoundsSection({
  targetField,
}: {
  targetField: "task" | "project";
}): React.ReactElement {
  const context = useRecordChromeContext();
  return <RecordRoundsPane targetField={targetField} targetId={context.recordId} />;
}

/** The proposals-owned round pane contributed to project and task records. */
function RecordRoundsPane({
  targetField,
  targetId,
}: {
  targetField: "task" | "project";
  targetId: string;
}): React.ReactElement {
  const t = useProposalsT();
  const routeHref = useRouteHref();
  const columns = React.useMemo<readonly ListColumn<RoundPaneRow>[]>(
    () => [
      { field: "name", header: t("common.name") },
      { field: "status", header: t("common.status"), widget: "statusBadge" },
      { field: "last_call_at" },
      { field: "submission_deadline" },
      { field: "facilitator" },
    ],
    [t],
  );
  return (
    <ListView<RoundPaneRow>
      resource={ROUND_MODEL}
      scope="local"
      fields={[
        "id",
        "name",
        "status",
        "last_call_at",
        "submission_deadline",
        "facilitator",
      ]}
      baseFilter={{ [targetField]: { exact: targetId } }}
      order={{ submission_deadline: "ASC" }}
      columns={columns}
      rowHref={(row) => routeHref("proposals.rounds.record", { id: row.id })}
      emptyContent={t("round.pane.empty")}
    />
  );
}

function RoundTabLabel({ name }: { name: "roster" | "comparison" }): React.ReactElement {
  const t = useProposalsT();
  return <>{t(name === "roster" ? "round.tabs.roster" : "round.tabs.comparison")}</>;
}

export const roundRecordSlots = [
  {
    ...formViewRecordActionsSlot(ROUND_MODEL), id: "proposals.round-open", sequence: 30,
    requiredFields: ROUND_RECORD_FIELDS,
    content: <RoundRecordSection surface="primary" />,
  },
  {
    ...formViewRecordActionsSlot(PROJECT_MODEL), id: "proposals.project-round-open", sequence: 30,
    requiredFields: PROJECT_ROUND_FIELDS,
    content: <ProjectRoundRecord surface="primary" />,
  },
  {
    ...formViewRecordActionsSlot(ROUND_MODEL), id: "proposals.round-verbs", sequence: 35,
    requiredFields: ROUND_RECORD_FIELDS,
    recordActionPlacement: "menu", content: <RoundRecordSection surface="actions" />,
  },
  {
    ...formViewRecordActionsSlot(PROJECT_MODEL), id: "proposals.project-round-verbs", sequence: 35,
    requiredFields: PROJECT_ROUND_FIELDS,
    recordActionPlacement: "menu", content: <ProjectRoundRecord surface="actions" />,
  },
  {
    ...formViewSectionsSlot(ROUND_MODEL), id: "proposals.round-people", sequence: 40,
    content: <Tab id="round-roster" label={<RoundTabLabel name="roster" />} requiredFields={ROUND_RECORD_FIELDS}><RoundRecordSection surface="people" /></Tab>,
  },
  {
    ...formViewSectionsSlot(PROJECT_MODEL), id: "proposals.project-people", sequence: 40,
    content: <Tab id="round-roster" label={<RoundTabLabel name="roster" />} requiredFields={PROJECT_ROUND_FIELDS}><ProjectRoundRecord surface="people" /></Tab>,
  },
  {
    ...formViewSectionsSlot(PROJECT_MODEL), id: "proposals.project-approach", sequence: 35,
    content: <Tab id="comparison" label={<RoundTabLabel name="comparison" />} requiredFields={PROJECT_ROUND_FIELDS}><ProjectRoundRecord surface="approach" /></Tab>,
  },
  {
    ...formViewRecordActionsSlot(TASK_MODEL),
    id: "proposals.task-responder-share",
    sequence: 45,
    requiredFields: ["revision", "permissions", "shared_with_responders", "project.source_proposal.id"],
    content: <TaskResponderShareAction />,
  },
  {
    ...formViewSectionsSlot(PROJECT_MODEL),
    id: "proposals.project-rounds",
    sequence: 35,
    content: (
      <Tab
        id="proposal-rounds"
        label={{ namespace: "proposals", key: "round.pane.label", fallback: enProposalsMessages["round.pane.label"] }}
        icon={<Glyph decorative name="proposals-round" />}
      >
        <RecordRoundsSection targetField="project" />
      </Tab>
    ),
  },
  {
    ...formViewSectionsSlot(TASK_MODEL),
    id: "proposals.task-rounds",
    sequence: 35,
    content: (
      <Tab
        id="proposal-rounds"
        label={{ namespace: "proposals", key: "round.pane.label", fallback: enProposalsMessages["round.pane.label"] }}
        icon={<Glyph decorative name="proposals-round" />}
      >
        <RecordRoundsSection targetField="task" />
      </Tab>
    ),
  },
] as const;
