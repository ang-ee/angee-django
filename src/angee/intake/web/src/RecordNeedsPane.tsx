import {
  ListView,
  NavLink,
  useResourceRecordHrefLookup,
  mutationDialogValueCodecs,
  useActionOutcomeMutation,
  useDescriptorRowActions,
  type ListColumn,
  type StringIdRow,
} from "@angee/ui";
import { TASK_MODEL } from "@angee/projects";
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
  const [convert, convertState] = useActionOutcomeMutation("convert_need_to_task", {
    idArgument: "need",
    invalidateModels: [NEED_MODEL, TASK_MODEL],
  });
  const actions = useDescriptorRowActions<NeedRow>([{
    id: "convert-need-to-task",
    label: t("needs.convert"),
    permission: "write",
    primary: true,
    disabled: convertState.fetching,
    visibleWhen: (record) => record.task === null,
    args: [{ name: "queue", label: t("needs.queue"), argKind: "relation", resource: "work.Queue" }],
    submit: (values, context) => convert(
      mutationDialogValueCodecs.requiredString(context.record?.id, "need"),
      { queue: mutationDialogValueCodecs.requiredString(values.queue, "queue") },
    ),
  }]);
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
          return href ? <NavLink href={href} variant="inline">{t("needs.evidence")}</NavLink> : "—";
        },
      },
      { field: "created_at", header: t("needs.createdAt") },
    ],
    [recordHref, t],
  );

  return (
    <>
      <ListView<NeedRow>
        resource={NEED_MODEL}
        scope="local"
        fields={[
          "id",
          "task.id",
          "permissions",
          "body",
          "party.display_name",
          "importance",
          "source_message.id",
          "created_at",
        ]}
        baseFilter={
          targetField === "project"
            ? { OR: [{ project: { exact: targetId } }, { "task.project": { exact: targetId } }] }
            : { task: { exact: targetId } }
        }
        defaultGroup={{ field: "party" }}
        order={{ created_at: "DESC" }}
        columns={columns}
        rowActions={actions.rowActions}
        toolbarActions={
          <CaptureNeedAction targetModel={targetModel} targetId={targetId} />
        }
        emptyContent={t("needs.empty")}
      />
      {actions.dialog}
    </>
  );
}
