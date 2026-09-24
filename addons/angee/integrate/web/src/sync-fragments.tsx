import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action,
  Column,
  Field,
  Group,
  useRecordActionMutation,
} from "@angee/ui";
import type { ReactElement, ReactNode } from "react";

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
  if (name === "sync_progress" || name === "last_sync_summary") return "json";
  if (name === "last_sync_status") return "statusBadge";
  if (name === "is_syncing") return "booleanBadge";
  return undefined;
}
