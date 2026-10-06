import {
  Badge,
  Column,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  useEnumOptions,
  useRouteHref,
  useRouteRecordId,
  type EditableLineField,
  type EditableLineSupplementalColumn,
  type RecordSmartButtonDescriptor,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { useWorkT } from "../i18n";
import { QUEUE_MODEL, STAGE_MODEL } from "../resources";
import { SYSTEM_STAGE_CATEGORIES } from "../stage-filters";

/** Stage columns shown at first; the rule flags stay in the lines' header menu of visible fields. */
const STAGE_PRIMARY_FIELDS = ["name", "category", "tone"] as const;

/** Queue collection and routed settings record: one stacked form, its stages one ordered section. */
export function QueuesPage(): React.ReactElement {
  const t = useWorkT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const recordId = useRouteRecordId();
  const visibilityOptions = useEnumOptions(QUEUE_MODEL, "visibility");
  const estimateOptions = useEnumOptions(QUEUE_MODEL, "estimate_scale");
  const stageFields = useStageLineFields();
  const stageBadges = useStageBadges();
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
      <Form
        resource={QUEUE_MODEL}
        linesTabLabel={t("queue.stages.title")}
        linePrimaryFields={STAGE_PRIMARY_FIELDS}
        lineFields={stageFields}
        lineSupplementalColumns={stageBadges}
      >
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

/**
 * The stage cells' headers, help and choices. A new or custom stage picks only a
 * custom category; a system stage's category cell is locked by the backend and
 * still reads its own label.
 */
function useStageLineFields(): readonly EditableLineField[] {
  const t = useWorkT();
  const categories = useEnumOptions(STAGE_MODEL, "category");
  return React.useMemo<readonly EditableLineField[]>(() => [
    { name: "name", label: t("common.name") },
    {
      name: "category",
      label: t("common.category"),
      options: categories.map((option) => ({
        ...option,
        disabled: (SYSTEM_STAGE_CATEGORIES as readonly string[]).includes(String(option.value)),
      })),
    },
    { name: "tone", label: t("common.tone") },
    { name: "rule_owned", label: t("stage.ruleOwned"), description: t("stage.ruleOwned.help") },
    { name: "conceals", label: t("stage.conceals"), description: t("stage.conceals.help") },
  ], [categories, t]);
}

/** Each stage's rule behaviour as badges, read from the live row. */
function useStageBadges(): readonly EditableLineSupplementalColumn[] {
  const t = useWorkT();
  return React.useMemo<readonly EditableLineSupplementalColumn[]>(() => [{
    key: "behaviour",
    header: t("stage.behaviour"),
    minWidth: 160,
    align: "left",
    render: (row) => (
      <div className="flex h-8 flex-wrap items-center gap-1">
        {row.rule_owned ? <Badge tone="info" density="compact">{t("stage.ruleOwned")}</Badge> : null}
        {row.conceals ? <Badge tone="neutral" density="compact">{t("stage.conceals")}</Badge> : null}
      </div>
    ),
  }], [t]);
}
