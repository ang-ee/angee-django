import {
  ListView,
  TextLink,
  useResourceRecordHrefLookup,
  type ListColumn,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { CaptureNeedAction } from "./CaptureNeedAction";
import { useIntakeT } from "./i18n";
import { MESSAGE_MODEL, NEED_MODEL } from "./resources";

interface NeedRow extends StringIdRow {
  body?: string;
  importance?: string;
  party?: { display_name?: string } | null;
  source_message?: { id?: string } | null;
  created_at?: string;
}

export interface RecordNeedsPaneProps {
  targetModel: string;
  targetField: "task" | "project";
  targetId: string;
}

/** Data-bound intake pane composed into a project/task FormView record tab. */
export function RecordNeedsPane({
  targetModel,
  targetField,
  targetId,
}: RecordNeedsPaneProps): React.ReactElement {
  const t = useIntakeT();
  const recordHref = useResourceRecordHrefLookup();
  const columns = React.useMemo<readonly ListColumn<NeedRow>[]>(
    () => [
      { field: "body", header: t("needs.body") },
      { field: "party.display_name", header: t("needs.party") },
      {
        field: "importance",
        header: t("needs.importance"),
        widget: "statusBadge",
      },
      {
        field: "source_message.id",
        header: t("needs.evidence"),
        render: (row) => {
          const sourceId = row.source_message?.id;
          const href = sourceId
            ? recordHref(MESSAGE_MODEL, sourceId)
            : undefined;
          return href ? <TextLink href={href}>{t("needs.evidence")}</TextLink> : "—";
        },
      },
      { field: "created_at", header: t("needs.createdAt") },
    ],
    [recordHref, t],
  );

  return (
    <ListView<NeedRow>
      resource={NEED_MODEL}
      scope="local"
      fields={[
        "id",
        "body",
        "party.display_name",
        "importance",
        "source_message.id",
        "created_at",
      ]}
      baseFilter={{ [targetField]: { exact: targetId } }}
      defaultGroup={{ field: "party" }}
      order={{ created_at: "DESC" }}
      columns={columns}
      toolbarActions={
        <CaptureNeedAction targetModel={targetModel} targetId={targetId} />
      }
      emptyContent={t("needs.empty")}
    />
  );
}
