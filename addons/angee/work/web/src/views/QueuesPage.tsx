import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  SettingsSection,
  SettingsShell,
  useEnumOptions,
  useRouteHref,
  useRouteRecordId,
  type RecordPanelContext,
  type RecordSmartButtonDescriptor,
  type RecordTabDescriptor,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { useWorkT } from "../i18n";
import { QUEUE_MODEL, STAGE_MODEL } from "../resources";

const CUSTOM_STAGE_CATEGORIES = [
  "BACKLOG",
  "UNSTARTED",
  "STARTED",
  "COMPLETED",
  "CANCELED",
] as const;

/** Queue collection and routed settings record. */
export function QueuesPage(): React.ReactElement {
  const t = useWorkT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const recordId = useRouteRecordId();
  const visibilityOptions = useEnumOptions(QUEUE_MODEL, "visibility");
  const estimateOptions = useEnumOptions(QUEUE_MODEL, "estimate_scale");
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "stages",
        label: t("queue.stages.tab"),
        render: (context) => <QueueStagesTab {...context} />,
      },
    ],
    [t],
  );
  const smartButtons = React.useMemo<readonly RecordSmartButtonDescriptor[]>(
    () =>
      ([
        ["board", "work-board", "work.board", t("queue.open.board")],
        ["triage", "work-triage", "work.triage", t("queue.open.triage")],
        ["cycles", "work-cycle", "work.cycles", t("queue.open.cycles")],
      ] as const).map(([id, icon, route, label]) => ({
        id: `work-queue-${id}`,
        icon,
        count: "↗",
        label,
        disabled: !recordId || recordId === "new",
        onClick: () => {
          if (!recordId || recordId === "new") return;
          void navigate({ to: routeHref(route, { queueId: recordId }) });
        },
      })),
    [navigate, recordId, routeHref, t],
  );

  return (
    <ResourceList
      resource={QUEUE_MODEL}
      placement="inline"
      routed
      recordTabs={recordTabs}
      recordSmartButtons={smartButtons}
    >
      <List resource={QUEUE_MODEL} order={{ key: "ASC" }}>
        <Column field="key" header={t("common.key")} />
        <Column field="name" header={t("common.name")} />
        <Column field="triage_enabled" />
        <Column field="cycles_enabled" />
        <Column field="estimate_scale" />
        <Column field="updated_at" />
      </List>
      <Form resource={QUEUE_MODEL} layout="tabs">
        <Field name="name" title />
        <Group label={t("queue.group.identity")} columns={2}>
          <Field name="key" />
          <Field name="slug" />
          <Field name="visibility" options={visibilityOptions} />
          <Field name="parent" />
        </Group>
        <Field name="description" widget="textarea" body />
        <Group label={t("queue.group.triage")} columns={2}>
          <Field name="triage_enabled" />
          <Field name="default_stage" />
          <Field name="auto_archive_months" />
          <Field name="auto_close_months" />
        </Group>
        <Group label={t("queue.group.cadence")} columns={2}>
          <Field name="cycles_enabled" />
          <Field name="cycle_weeks" />
          <Field name="cycle_cooldown_weeks" />
          <Field
            name="cycle_start_day"
            widget="integer"
            description={t("queue.cycleStart.help")}
          />
          <Field name="upcoming_cycle_count" />
        </Group>
        <Group label={t("queue.group.estimates")} columns={2}>
          <Field name="estimate_scale" options={estimateOptions} />
          <Field name="estimate_allow_zero" />
          <Field name="default_estimate" />
        </Group>
      </Form>
    </ResourceList>
  );
}

function QueueStagesTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useWorkT();
  const categoryOptions = useEnumOptions(STAGE_MODEL, "category").filter((option) =>
    CUSTOM_STAGE_CATEGORIES.includes(
      String(option.value).toUpperCase() as (typeof CUSTOM_STAGE_CATEGORIES)[number],
    ),
  );
  return (
    <SettingsShell maxWidth="1100" gap="8">
      <SettingsSection
        title={t("queue.stages.system.title")}
        description={t("queue.stages.system.description")}
      >
        <List
          resource={STAGE_MODEL}
          scope="local"
          baseFilter={{
            queue: { exact: recordId },
            category: { inList: ["TRIAGE", "DUPLICATE"] },
          }}
          order={{ position: "ASC" }}
        >
          <Column field="name" header={t("common.name")} />
          <Column field="category" header={t("common.category")} />
          <Column field="tone" header={t("common.tone")} />
          <Column field="position" header={t("common.order")} />
        </List>
      </SettingsSection>
      <SettingsSection
        title={t("queue.stages.custom.title")}
        description={t("queue.stages.custom.description")}
      >
        <DrawerResourceList
          resource={STAGE_MODEL}
          createDefaults={{ queue: recordId }}
        >
          <List
            resource={STAGE_MODEL}
            baseFilter={{
              queue: { exact: recordId },
              category: { inList: CUSTOM_STAGE_CATEGORIES },
            }}
            order={{ position: "ASC" }}
          >
            <Column field="name" header={t("common.name")} />
            <Column field="category" header={t("common.category")} />
            <Column field="tone" header={t("common.tone")} />
            <Column field="position" header={t("common.order")} />
          </List>
          <Form resource={STAGE_MODEL}>
            <Field name="name" title />
            <Field name="queue" readOnly />
            <Field name="category" options={categoryOptions} />
            <Field name="tone" />
            <Field name="position" />
          </Form>
        </DrawerResourceList>
      </SettingsSection>
    </SettingsShell>
  );
}
