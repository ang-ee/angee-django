import { ActivityAgendaList } from "@angee/messaging";
import { Link } from "@tanstack/react-router";
import {
  Button,
  Column,
  List,
  PageBody,
  PageHeader,
  SectionEyebrow,
  formatDateStorage,
  useRouteHref,
  useRuntimeAuth,
} from "@angee/ui";
import * as React from "react";

import { useProjectsT } from "../i18n";
import { TASK_MODEL } from "../resources";
import { useTaskRowActions, type TaskActionRow } from "../task-actions";

/** The personal projection: assigned tasks plus the messaging-owned activity agenda. */
export function MyWorkPage(): React.ReactElement {
  const t = useProjectsT();
  const auth = useRuntimeAuth();
  const routeHref = useRouteHref();
  const taskActions = useTaskRowActions<TaskActionRow>();
  const window = React.useMemo(activityWindow, []);
  const taskFilter = auth.user
    ? {
        assignee: { exact: auth.user.id },
        status: { exact: "OPEN" },
      }
    : { id: { inList: [] } };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader title={t("myWork.title")} description={t("myWork.description")} />
      <PageBody className="flex flex-col gap-6">
        <section className="flex flex-col gap-2">
          <SectionEyebrow as="h2">{t("myWork.tasks")}</SectionEyebrow>
          <List<TaskActionRow>
            resource={TASK_MODEL}
            scope="local"
            baseFilter={taskFilter}
            order={{ due_date: "ASC", sort_order: "ASC" }}
            rowActions={taskActions}
            rowHref={(row) => routeHref("projects.tasks.record", { id: row.id })}
            emptyContent={{
              icon: "my-work",
              title: t("myWork.empty.tasks"),
              description: t("myWork.empty.tasksDescription"),
              // Two ways on rather than a dead end: the board is where work is
              // picked up, the list is where it is found.
              actions: (
                <>
                  <Button asChild variant="primary" size="sm">
                    <Link to={routeHref("projects.board")}>
                      {t("myWork.empty.openBoard")}
                    </Link>
                  </Button>
                  <Button asChild variant="secondary" size="sm">
                    <Link to={routeHref("projects.tasks")}>
                      {t("myWork.empty.browseTasks")}
                    </Link>
                  </Button>
                </>
              ),
            }}
          >
            <Column field="title" />
            <Column field="project.title" header={t("common.project")} />
            <Column field="priority" header={t("common.priority")} widget="priority" />
            <Column field="due_date" header={t("common.dueDate")} />
          </List>
        </section>
        <section className="flex flex-col gap-2">
          <div>
            <SectionEyebrow as="h2">{t("myWork.activities")}</SectionEyebrow>
            <p className="text-13 text-fg-muted">{t("myWork.activitiesHint")}</p>
          </div>
          <ActivityAgendaList
            windowStart={window.start}
            windowEnd={window.end}
          />
        </section>
      </PageBody>
    </div>
  );
}

function activityWindow(): { start: string; end: string } {
  const end = new Date();
  end.setDate(end.getDate() + 31);
  return {
    start: "1970-01-01",
    end: formatDateStorage(end) ?? "2100-01-01",
  };
}
