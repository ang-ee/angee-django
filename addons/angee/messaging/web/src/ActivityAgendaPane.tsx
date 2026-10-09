import { useAuthoredQuery } from "@angee/refine";
import {
  EmptyState,
  ErrorBanner,
  MiniCard,
  ScrollArea,
  SectionHeading,
  Skeleton,
  SkeletonStatus,
  NavLink,
  formatDate,
  formatDateStorage,
  useResourceRecordHrefLookup,
  useStatusTone,
} from "@angee/ui";
import * as React from "react";

import { ACTIVITY_STATE_TONES, activityStateLabel } from "./activity-state";
import {
  ACTIVITY_AGENDA_MODELS,
  ActivityAgendaDocument,
} from "./documents";
import { useMessagingT } from "./i18n";

/** Days after today the agenda covers; every overdue activity is always included. */
const AGENDA_DAYS_AHEAD = 30;

/**
 * The actor's activities due — every overdue activity plus those due in the next
 * 30 days — as a compact pane. Messaging owns the query, the window, the items and
 * their record links; a page publishes it into the console's primary (left) pane
 * with `usePrimaryPane`, which owns collapse and the narrow-screen drawer.
 */
export function ActivityAgendaPane(): React.ReactElement {
  const t = useMessagingT();
  const recordHref = useResourceRecordHrefLookup();
  const statusTone = useStatusTone();
  const agendaWindowVariables = React.useMemo(agendaWindow, []);
  const agenda = useAuthoredQuery(ActivityAgendaDocument, agendaWindowVariables, {
    models: ACTIVITY_AGENDA_MODELS,
  });
  const activities = agenda.data?.activity_agenda;
  const title = t("agenda.title");

  let body: React.ReactNode;
  if (agenda.error) {
    body = <ErrorBanner description={agenda.error.message} />;
  } else if (activities === undefined) {
    body = (
      <SkeletonStatus label={t("activity.loading")}>
        <div aria-hidden="true" className="grid gap-2">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-14 w-full" />
          ))}
        </div>
      </SkeletonStatus>
    );
  } else if (activities.length === 0) {
    body = (
      <EmptyState
        icon="activity"
        title={t("agenda.emptyTitle")}
        description={t("agenda.hint")}
        className="min-h-40 p-4"
      />
    );
  } else {
    body = (
      <ul className="grid gap-2">
        {activities.map((activity) => {
          const { label, model_label: model, record_id: id } = activity.attachment;
          const href = recordHref(model, id);
          return (
            <li key={activity.id}>
              <MiniCard
                title={activity.summary}
                meta={
                  <>
                    {href ? <NavLink href={href} variant="inline">{label}</NavLink> : label}
                    {activity.due_date ? ` · ${formatDate(activity.due_date)}` : null}
                  </>
                }
                primaryTag={
                  Object.hasOwn(ACTIVITY_STATE_TONES, activity.state)
                    ? {
                        label: activityStateLabel(activity, t),
                        tone: statusTone(activity.state, ACTIVITY_STATE_TONES),
                      }
                    : undefined
                }
              />
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <section aria-label={title} className="flex h-full min-h-0 flex-col">
      <SectionHeading
        as="h2"
        className="border-b border-border-subtle px-3 py-2"
        label={title}
        count={activities === undefined ? undefined : `· ${activities.length}`}
      />
      <ScrollArea className="min-h-0 flex-1" viewportClassName="overflow-x-hidden p-2">
        {body}
      </ScrollArea>
    </section>
  );
}

/** `[start, end)` dates: everything overdue through the next `AGENDA_DAYS_AHEAD` days. */
function agendaWindow(): { windowStart: string; windowEnd: string } {
  const end = new Date();
  end.setDate(end.getDate() + AGENDA_DAYS_AHEAD + 1);
  return {
    windowStart: "1970-01-01",
    windowEnd: formatDateStorage(end) ?? "2100-01-01",
  };
}
