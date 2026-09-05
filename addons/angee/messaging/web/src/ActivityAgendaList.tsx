import { useAuthoredQuery } from "@angee/refine";
import {
  RowsListView,
  TextLink,
  useResourceRecordHrefLookup,
  type ListColumn,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import {
  ACTIVITY_AGENDA_MODELS,
  ActivityAgendaDocument,
} from "./documents";
import { useMessagingT } from "./i18n";

interface ActivityAgendaRow extends StringIdRow {
  summary: string;
  dueDate: string | null;
  state: string;
  targetLabel: string;
  targetModel: string;
  targetId: string;
}

export interface ActivityAgendaListProps {
  windowStart: string;
  windowEnd: string;
}

/** Messaging-owned activity query, row projection, and record-link rendering. */
export function ActivityAgendaList({
  windowStart,
  windowEnd,
}: ActivityAgendaListProps): React.ReactElement {
  const t = useMessagingT();
  const recordHref = useResourceRecordHrefLookup();
  const agenda = useAuthoredQuery(
    ActivityAgendaDocument,
    { windowStart, windowEnd },
    { models: ACTIVITY_AGENDA_MODELS },
  );
  const rows = React.useMemo<readonly ActivityAgendaRow[]>(
    () =>
      (agenda.data?.activity_agenda ?? []).map((activity) => ({
        id: activity.id,
        summary: activity.summary,
        dueDate: activity.due_date ?? null,
        state: activity.state,
        targetLabel: activity.attachment.label,
        targetModel: activity.attachment.model_label,
        targetId: activity.attachment.record_id,
      })),
    [agenda.data?.activity_agenda],
  );
  const columns = React.useMemo<readonly ListColumn<ActivityAgendaRow>[]>(
    () => [
      { field: "summary", header: t("agenda.summary") },
      { field: "dueDate", header: t("agenda.dueDate") },
      { field: "state", header: t("agenda.state") },
      {
        field: "targetLabel",
        header: t("agenda.target"),
        render: (row) => {
          const href = recordHref(row.targetModel, row.targetId);
          return href ? (
            <TextLink href={href}>{row.targetLabel}</TextLink>
          ) : (
            row.targetLabel
          );
        },
      },
    ],
    [recordHref, t],
  );

  return (
    <RowsListView
      rows={rows}
      columns={columns}
      scope="local"
      fetching={agenda.isFetching}
      error={agenda.error}
      emptyContent={t("agenda.empty")}
    />
  );
}
