import {
  Badge,
  Field,
  Form,
  Group,
  useEnumOptions,
  type ContainerEntry,
  type EditableLineField,
  type EditableLineSupplementalColumn,
  type FormProps,
} from "@angee/ui";
import * as React from "react";

import { useWorkT } from "./i18n";
import { QUEUE_MODEL, STAGE_MODEL } from "./resources";
import { SYSTEM_STAGE_CATEGORIES } from "./stage-filters";

/** The `work.Queue#sections` child holding the queue's Stages lines; products narrow to it by this id. */
export const QUEUE_STAGES_SECTION = "work.queue-stages";

function WorkText({ id }: { id: string }): React.ReactElement {
  const t = useWorkT();
  return <>{t(id)}</>;
}

/**
 * The queue settings sections, declared once as `work.Queue#sections` children so a
 * product narrows them per app or route with `only` under `when`. Their order is the
 * queue form's: identity (with the description), triage, cadence, estimates, then the
 * Stages lines.
 */
export const QUEUE_FORM_SECTIONS: ContainerEntry<React.ReactNode> = {
  "work.queue-identity": {
    sequence: 10,
    content: (
      <Group label={<WorkText id="queue.group.identity" />} columns={2}>
        <Field name="description" widget="textarea" body />
        <Field name="key" />
        <Field name="slug" />
        <Field name="visibility" />
        <Field name="parent" />
      </Group>
    ),
  },
  "work.queue-triage": {
    sequence: 20,
    content: (
      <Group label={<WorkText id="queue.group.triage" />} columns={2}>
        <Field name="triage_enabled" />
        <Field name="default_stage" />
        <Field name="auto_archive_months" />
        <Field name="auto_close_months" />
      </Group>
    ),
  },
  "work.queue-cadence": {
    sequence: 30,
    content: (
      <Group label={<WorkText id="queue.group.cadence" />} columns={2}>
        <Field name="cycles_enabled" />
        <Field name="cycle_weeks" />
        <Field name="cycle_cooldown_weeks" />
        <Field name="cycle_start_day" widget="integer" description={<WorkText id="queue.cycleStart.help" />} />
        <Field name="upcoming_cycle_count" />
      </Group>
    ),
  },
  "work.queue-estimates": {
    sequence: 40,
    content: (
      <Group label={<WorkText id="queue.group.estimates" />} columns={2}>
        <Field name="estimate_scale" />
        <Field name="estimate_allow_zero" />
        <Field name="default_estimate" />
      </Group>
    ),
  },
  [QUEUE_STAGES_SECTION]: {
    sequence: 50,
    content: <Group label={<WorkText id="queue.stages.title" />} lines />,
  },
};

/** Stage columns shown at first; the rule flags stay in the lines' header menu of visible fields. */
const STAGE_PRIMARY_FIELDS = ["name", "category", "tone"] as const;

/**
 * The queue settings form: its title, its declared sections and its stage lines'
 * presentation, one owner for the queue page's collection record and for a product
 * that shows one queue's settings on its own route. Pass FormView props (`id`,
 * `contextLine`, …); the element also composes inside `ResourceList`.
 */
export function useQueueFormDeclaration(props: Omit<FormProps, "resource" | "children"> = {}): React.ReactElement {
  const t = useWorkT();
  const categories = useEnumOptions(STAGE_MODEL, "category");
  const lineFields = React.useMemo<readonly EditableLineField[]>(() => [
    { name: "name", label: t("common.name") },
    {
      // A new or custom stage picks only a custom category; a system stage's category
      // cell is locked by the backend and still reads its own label.
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
  // Each stage's rule behaviour as badges, read from the live row.
  const stageBadges = React.useMemo<readonly EditableLineSupplementalColumn[]>(() => [{
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
  return (
    <Form
      {...props}
      resource={QUEUE_MODEL}
      linePrimaryFields={STAGE_PRIMARY_FIELDS}
      lineFields={lineFields}
      lineSupplementalColumns={stageBadges}
    >
      <Field name="name" title />
    </Form>
  );
}

/** One queue's settings on a product's own route, narrowed there through `work.Queue#sections`. */
export function QueueSettingsForm(props: Omit<FormProps, "resource" | "children">): React.ReactElement {
  return useQueueFormDeclaration(props);
}
