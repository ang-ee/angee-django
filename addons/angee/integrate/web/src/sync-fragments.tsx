import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action,
  Column,
  Field,
  Group,
  JsonValueView,
  TextLink,
  jsonObjectFromUnknown,
  useResourceRecordHrefLookup,
  useRecordActionMutation,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";
import type { ReactElement, ReactNode } from "react";

import { useIntegrateT } from "./i18n";

export type IntegrationSyncFieldName =
  | "is_syncing"
  | "sync_stage"
  | "sync_error"
  | "sync_progress"
  | "last_sync_summary"
  | "last_sync_status"
  | "last_sync_items"
  | "last_sync_completed_at";

export interface IntegrationSyncFragmentOptions {
  fields?: readonly IntegrationSyncFieldName[];
  labels?: Partial<Record<IntegrationSyncFieldName, ReactNode>>;
}

export interface IntegrationSyncFieldsOptions extends IntegrationSyncFragmentOptions {
  label: ReactNode;
}

const DEFAULT_SYNC_FIELDS: readonly IntegrationSyncFieldName[] = [
  "is_syncing",
  "sync_stage",
  "sync_error",
  "sync_progress",
  "last_sync_summary",
  "last_sync_status",
  "last_sync_items",
  "last_sync_completed_at",
];

/** Declarative integration runtime fields shared by every integration subtype. */
export function IntegrationSyncFields({
  label,
  fields = DEFAULT_SYNC_FIELDS,
  labels,
}: IntegrationSyncFieldsOptions): ReactElement {
  return (
    <Group label={label} columns={2}>
      {fields.map((name) => (
        <Field
          key={name}
          name={name}
          label={labels?.[name]}
          widget={syncWidget(name)}
          readOnly
        />
      ))}
    </Group>
  );
}

/** Declarative integration runtime columns with the same status presentation. */
export function IntegrationSyncColumns({
  fields = ["sync_stage", "last_sync_status", "last_sync_items", "last_sync_completed_at"],
  labels,
}: IntegrationSyncFragmentOptions = {}): readonly ReactElement[] {
  return fields.map((name) => (
    <Column
      key={name}
      field={name}
      header={labels?.[name]}
      widget={name === "last_sync_status" ? "statusBadge" : undefined}
    />
  ));
}

/** One record-scoped sync action backed by the standard action mutation owner. */
export function useIntegrationSyncAction(
  action: ActionFieldName,
  label: ReactNode,
): ReactElement {
  const [sync] = useRecordActionMutation<ActionFieldName>(action);
  return <Action id="sync" label={label} icon="refresh" run={sync} />;
}

function syncWidget(name: IntegrationSyncFieldName): string | undefined {
  if (name === "sync_progress") return "integrationSyncProgress";
  if (name === "last_sync_summary") return "json";
  if (name === "last_sync_status") return "statusBadge";
  if (name === "is_syncing") return "booleanBadge";
  return undefined;
}

/** One workflow run link shared by bridge progress and stream inspection. */
export function IntegrationSyncRunLink({ value }: { value: unknown }): ReactElement | null {
  const t = useIntegrateT();
  const recordHref = useResourceRecordHrefLookup();
  const details = jsonObjectFromUnknown(jsonObjectFromUnknown(value)?.details);
  const run = details?.run;
  const href = typeof run === "string" ? recordHref("workflows.WorkflowRun", run) : undefined;
  return href ? <TextLink href={href}>{t("sync.openRun")}</TextLink> : null;
}

function SyncProgress({ value }: WidgetRenderProps): ReactElement {
  return (
    <div className="space-y-2">
      <IntegrationSyncRunLink value={value} />
      <JsonValueView value={value} />
    </div>
  );
}

/** Bridge progress composes the host's workflow route when it is available. */
export const integrationSyncProgressWidget = {
  read: SyncProgress,
} satisfies WidgetDefinition;
