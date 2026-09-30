import { decisionRecordTab } from "@angee/decisions";
import { defineBaseAddon } from "@angee/app";
import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner,
  Glyph,
  Group,
  Skeleton,
  SkeletonStatus,
  Tab,
  formViewRecordActionsSlot,
  formViewSectionsSlot,
  useRecordChromeContext,
  type ChatterViewContext,
} from "@angee/ui";
import { MessageSquareQuote } from "lucide-react";
import type { ReactElement } from "react";

import { enIntakeMessages, useIntakeT } from "./i18n";
import { TaskAccessDecisions } from "./TaskAccessDecisions";
import { TaskAccessActions } from "./TaskAccessActions";
import { TaskAccessNeedsDocument } from "./documents";
import { NEED_MODEL } from "./resources";
import { RecordNeedsPane } from "./RecordNeedsPane";

export { NEED_MODEL } from "./resources";

const intake = defineBaseAddon({
  id: "intake",
  i18n: { intake: enIntakeMessages },
  icons: { "intake-needs": MessageSquareQuote },
  chatter: [{
    id: "intake.access-decisions", label: "Decisions", sequence: 50,
    when: (context) => context.view.kind === "record" && context.route?.modelLabel === TASK_MODEL,
    render: (context) => <TaskAccessChatter context={context} />,
  }],
  slots: [
    decisionRecordTab(NEED_MODEL),
    {
      ...formViewSectionsSlot(TASK_MODEL), id: "intake.task-access-decisions", sequence: 50,
      // Access decisions are the request's writers' business; a requester reading their own request never sees them.
      permission: "write", requiredFields: ["permissions"],
      content: <Group label={<AccessLabel />} savedOnly content={<TaskAccessGroup />} />,
    },
    {
      ...formViewRecordActionsSlot(TASK_MODEL), id: "intake.task-access-actions", sequence: 50,
      recordActionPlacement: "menu", permission: "write", requiredFields: ["permissions"],
      content: <TaskAccessActions />,
    },
    {
      ...formViewSectionsSlot(PROJECT_MODEL),
      id: "intake.project-needs",
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
    {
      ...formViewSectionsSlot(TASK_MODEL),
      id: "intake.task-needs",
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
  ],
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
  const { recordId } = useRecordChromeContext();
  const t = useIntakeT();
  const query = useAuthoredQuery(TaskAccessNeedsDocument, { task: recordId }, {
    models: [NEED_MODEL, "decisions.Decision"],
  });
  if (query.isFetching && !query.data) return <SkeletonStatus label={t("access.label")}>
    <Skeleton className="h-16 w-full" />
  </SkeletonStatus>;
  if (query.error) return <ErrorBanner description={t("access.error")} />;
  return <TaskAccessDecisions needs={query.data?.intake_needs ?? []} />;
}

function TaskAccessChatter({ context }: { context: ChatterViewContext }): ReactElement {
  const t = useIntakeT();
  const task = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const query = useAuthoredQuery(TaskAccessNeedsDocument, { task }, {
    enabled: Boolean(task), models: [NEED_MODEL, "decisions.Decision"],
  });
  if (query.isFetching && !query.data) return <SkeletonStatus label={t("access.label")}>
    <Skeleton className="h-16 w-full" />
  </SkeletonStatus>;
  if (query.error) return <ErrorBanner description={t("access.error")} />;
  return <TaskAccessDecisions needs={query.data?.intake_needs ?? []} />;
}

export default intake;

function AccessLabel(): ReactElement {
  const t = useIntakeT();
  return <>{t("access.label")}</>;
}
