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
import { ROUND_MODEL } from "./resources";
import { TaskResponderShareAction } from "./task-responder-share";

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

export const roundRecordSlots = [
  {
    ...formViewRecordActionsSlot(TASK_MODEL),
    id: "proposals.task-responder-share",
    sequence: 45,
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
