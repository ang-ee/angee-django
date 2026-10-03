import { decisionRecordTab } from "@angee/decisions";
import { defineBaseAddon } from "@angee/app";
import { holdsPermission } from "@angee/metadata";
import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import { ShareAccessRailGroup } from "@angee/iam";
import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner,
  Glyph,
  Group,
  SkeletonStatus,
  Tab,
  useRecordChromeContext,
  type ChatterViewContext,
} from "@angee/ui";
import { MessageSquareQuote } from "lucide-react";
import type { ReactElement } from "react";

import { enIntakeMessages, useIntakeT } from "./i18n";
import { TaskAccessCardSkeleton, TaskAccessDecisions } from "./TaskAccessDecisions";
import { TaskAccessNeedsDocument } from "./documents";
import { NEED_MODEL } from "./resources";
import { RecordNeedsPane } from "./RecordNeedsPane";
import { TaskRequesterAccessRole } from "./access-role";

export { NEED_MODEL } from "./resources";

const intake = defineBaseAddon({
  id: "intake",
  i18n: { intake: enIntakeMessages },
  icons: { "intake-needs": MessageSquareQuote },
  containers: {
    [`${TASK_MODEL}#access-roles`]: {
      "intake.requester": { content: TaskRequesterAccessRole },
    },
    [`${TASK_MODEL}#aside`]: {
      "intake.access-decisions": {
        sequence: 50,
        content: {
          label: "Decisions",
          when: (context) => context.view.kind === "record",
          render: (context) => <TaskAccessChatter context={context} />,
        },
      },
    },
    [`${NEED_MODEL}#sections`]: {
      "intake.decisions": decisionRecordTab(),
    },
    [`${TASK_MODEL}#rail`]: {
      "intake.people-rail": { sequence: 40, content: ShareAccessRailGroup },
    },
    [`${TASK_MODEL}#sections`]: {
      "intake.task-access-decisions": {
        sequence: 50,
        // Access decisions are the request's writers' business; a requester reading their own request never sees them.
        permission: "write",
        requiredFields: ["permissions"],
        content: <Group label={<AccessLabel />} hint={<AccessHint />} savedOnly collapsible defaultOpen content={<TaskAccessGroup />} />,
      },
      "intake.task-needs": {
        sequence: 30,
        content: (
          <Tab
            id="needs"
            label={{ namespace: "intake", key: "needs.label", fallback: enIntakeMessages["needs.label"] }}
            icon={<Glyph decorative name="intake-needs" />}
          >
            <RecordNeedsSection targetField="task" />
          </Tab>
        ),
      },
    },
    [`${PROJECT_MODEL}#sections`]: {
      "intake.project-needs": {
        sequence: 30,
        content: (
          <Tab
            id="needs"
            label={{ namespace: "intake", key: "needs.label", fallback: enIntakeMessages["needs.label"] }}
            icon={<Glyph decorative name="intake-needs" />}
          >
            <RecordNeedsSection targetField="project" />
          </Tab>
        ),
      },
    },
  },
});

function RecordNeedsSection({
  targetField,
}: {
  targetField: "task" | "project";
}): ReactElement {
  const context = useRecordChromeContext();
  return (
    <RecordNeedsPane
      targetModel={context.resource}
      targetField={targetField}
      targetId={context.recordId}
    />
  );
}


function TaskAccessGroup(): ReactElement {
  const { record, recordId } = useRecordChromeContext();
  const t = useIntakeT();
  const query = useAuthoredQuery(TaskAccessNeedsDocument, { task: recordId }, {
    models: [NEED_MODEL, "decisions.Decision"],
  });
  if (query.isFetching && !query.data) return <SkeletonStatus label={t("access.label")}>
    <TaskAccessCardSkeleton />
  </SkeletonStatus>;
  if (query.error) return <ErrorBanner description={t("access.error")} />;
  return <TaskAccessDecisions needs={query.data?.intake_needs ?? []}
    canManage={Boolean(record && holdsPermission(record, "write") && holdsPermission(record, "share"))} />;
}

function TaskAccessChatter({ context }: { context: ChatterViewContext }): ReactElement {
  const t = useIntakeT();
  const task = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const query = useAuthoredQuery(TaskAccessNeedsDocument, { task }, {
    enabled: Boolean(task), models: [NEED_MODEL, "decisions.Decision"],
  });
  if (query.isFetching && !query.data) return <SkeletonStatus label={t("access.label")}>
    <TaskAccessCardSkeleton />
  </SkeletonStatus>;
  if (query.error) return <ErrorBanner description={t("access.error")} />;
  return <TaskAccessDecisions needs={query.data?.intake_needs ?? []} />;
}

export default intake;

function AccessLabel(): ReactElement {
  const t = useIntakeT();
  return <>{t("access.label")}</>;
}

function AccessHint(): ReactElement {
  const t = useIntakeT();
  return <>{t("access.hint")}</>;
}
