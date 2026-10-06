import * as React from "react";
import {
  useList,
  type BaseRecord,
  type CrudFilter,
  type HttpError,
} from "@refinedev/core";
import { extractActionOutcome, refineFieldsFromPaths } from "@angee/refine";
import {
  holdsPermission,
  refineResourceName,
  rowPublicId,
  trashFlagField,
  type DataResourceMetadata,
  type Row,
} from "@angee/metadata";

import { usePrompt } from "../../feedback";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";
import { Collapsible } from "../../ui/collapsible";
import type { ActionDescriptor } from "../page/Action";
import { useActionResultRun } from "./action-result-run";
import { useAuthoredResourceMutation } from "./authored-resource-mutation";
import { RestoreRecord, TrashRecord } from "./documents";
import { defineRowAction, type RowActionDeclaration } from "./RowActions";

/** The record permission that offers trash and restore — the shared verbs' authority. */
export const TRASH_PERMISSION = "delete";

/** Mirrors the backend's `TRASH_REASON_MAX_LENGTH`. */
export const TRASH_REASON_MAX_LENGTH = 1000;

/** The removed-record stamps the backend's `TrashedRefMixin` projects. */
export const TRASH_STAMP_FIELDS = ["trashed_at", "trash_reason", "trashed_by_label"] as const;

/** Ask to move one record to the trash; resolves the optional reason, or `null` when kept. */
export type TrashPrompt = (name?: string) => Promise<{ reason: string } | null>;

/**
 * The one trash confirmation: a danger dialog with an optional reason and a
 * cancel label that names the record when the caller knows its name. Domain
 * verbs with their own transport (record chatter) compose it before their
 * mutation; resource records use {@link useTrashRecord}.
 */
export function useTrashPrompt(): TrashPrompt {
  const prompt = usePrompt();
  const t = useUiT();
  return React.useCallback<TrashPrompt>(async (name) => {
    const values = await prompt({
      title: name ? t("trash.titleNamed", { name }) : t("trash.title"),
      body: t("trash.body"),
      fields: [{
        name: "reason",
        label: t("trash.reason"),
        type: "textarea",
        placeholder: t("trash.reasonPlaceholder"),
        maxLength: TRASH_REASON_MAX_LENGTH,
      }],
      confirm: t("trash.confirm"),
      cancel: name ? t("trash.cancelNamed", { name }) : t("trash.cancel"),
      danger: true,
    });
    return values ? { reason: (values.reason ?? "").trim() } : null;
  }, [prompt, t]);
}

export interface UseTrashRecordResult {
  /** The resource is trashable and declares a REBAC resource type. */
  available: boolean;
  busy: boolean;
  /** Confirm, then move one record to the trash; resolves whether it moved. */
  trash: (id: string, name?: string) => Promise<boolean>;
  /** Take one record out of the trash; resolves whether it was restored. */
  restore: (id: string) => Promise<boolean>;
}

/**
 * Run the shared `trash_record` / `restore_record` verbs for one trashable
 * resource, settling their outcome through the shared toast owner and
 * refreshing the resource's caches. The server checks `delete` on the record.
 */
export function useTrashRecord(resource: DataResourceMetadata | null | undefined): UseTrashRecordResult {
  const askTrash = useTrashPrompt();
  const settle = useActionResultRun();
  const targetType = resource && trashFlagField(resource) ? resource.resourceType ?? null : null;
  const options = resource
    ? { dataProviderName: resource.schemaName, invalidateModels: [resource.modelLabel] }
    : {};
  const [trashRecord, trashState] = useAuthoredResourceMutation(TrashRecord, {
    ...options,
    shouldInvalidate: (data) => data?.trash_record.ok === true,
  });
  const [restoreRecord, restoreState] = useAuthoredResourceMutation(RestoreRecord, {
    ...options,
    shouldInvalidate: (data) => data?.restore_record.ok === true,
  });
  const trash = React.useCallback(async (id: string, name?: string): Promise<boolean> => {
    if (!targetType) return false;
    const answer = await askTrash(name);
    if (!answer) return false;
    const outcome = await settle(async () => extractActionOutcome(
      await trashRecord({ target_type: targetType, target_id: id, reason: answer.reason }),
      "trash_record",
    ));
    return outcome?.ok === true;
  }, [askTrash, settle, targetType, trashRecord]);
  const restore = React.useCallback(async (id: string): Promise<boolean> => {
    if (!targetType) return false;
    const outcome = await settle(async () => extractActionOutcome(
      await restoreRecord({ target_type: targetType, target_id: id }),
      "restore_record",
    ));
    return outcome?.ok === true;
  }, [restoreRecord, settle, targetType]);
  return {
    available: targetType !== null,
    busy: trashState.fetching || restoreState.fetching,
    trash,
    restore,
  };
}

export interface TrashActionOptions<TRow extends Row = Row> {
  /** The record's display name in the confirmation copy. */
  name?: (record: TRow) => string;
}

/**
 * Record-form verbs for a trashable resource: Trash on an untrashed record,
 * Restore on a trashed one, each offered only when the record projects the
 * `delete` permission. The form must read the trash flag (a hidden field).
 */
export function useTrashActions(
  resource: DataResourceMetadata | null | undefined,
  options: TrashActionOptions = {},
): readonly ActionDescriptor[] {
  const t = useUiT();
  const { available, trash, restore } = useTrashRecord(resource);
  const flag = trashFlagField(resource);
  const { name } = options;
  return React.useMemo<readonly ActionDescriptor[]>(() => {
    if (!available || flag === null) return [];
    return [
      {
        id: "trash",
        label: t("trash.action"),
        icon: "trash",
        danger: true,
        permission: TRASH_PERMISSION,
        visibleWhen: (record) => record[flag] !== true,
        run: async (context) => {
          const id = rowPublicId(context.record);
          if (id && context.record && await trash(id, name?.(context.record))) context.refresh();
        },
      },
      {
        id: "restore",
        label: t("trash.restore"),
        icon: "undo-2",
        permission: TRASH_PERMISSION,
        visibleWhen: (record) => record[flag] === true,
        run: async (context) => {
          const id = rowPublicId(context.record);
          if (id && await restore(id)) context.refresh();
        },
      },
    ];
  }, [available, flag, name, restore, t, trash]);
}

/** List-row verbs matching {@link useTrashActions}, for `ListView` `rowActions`. */
export function useTrashRowActions<TRow extends Row>(
  resource: DataResourceMetadata | null | undefined,
  options: TrashActionOptions<TRow> = {},
): readonly RowActionDeclaration<TRow>[] {
  const t = useUiT();
  const { available, trash, restore } = useTrashRecord(resource);
  const flag = trashFlagField(resource);
  const { name } = options;
  return React.useMemo<readonly RowActionDeclaration<TRow>[]>(() => {
    if (!available || flag === null) return [];
    const manages = (row: TRow) => holdsPermission(row, TRASH_PERMISSION);
    return [
      defineRowAction<TRow>({
        kind: "page",
        id: "trash",
        label: t("trash.action"),
        icon: "trash",
        variant: "ghost",
        placement: "menu",
        pendingPolicy: "disable-actions",
        visible: (row) => manages(row) && row[flag] !== true,
        onSelect: async (row) => {
          const id = rowPublicId(row);
          if (id) await trash(id, name?.(row));
        },
      }),
      defineRowAction<TRow>({
        kind: "page",
        id: "restore",
        label: t("trash.restore"),
        icon: "undo-2",
        variant: "ghost",
        pendingPolicy: "disable-actions",
        visible: (row) => manages(row) && row[flag] === true,
        onSelect: async (row) => {
          const id = rowPublicId(row);
          if (id) await restore(id);
        },
      }),
    ];
  }, [available, flag, name, restore, t, trash]);
}

export interface RemovedDisclosureProps {
  /** Removed records the reader manages; nothing renders at zero. */
  count: number;
  defaultOpen?: boolean;
  className?: string;
  children: React.ReactNode;
}

/** The collapsed "Removed (n)" section shared by every removed-records list. */
export function RemovedDisclosure({
  count,
  defaultOpen = false,
  className,
  children,
}: RemovedDisclosureProps): React.ReactElement | null {
  const t = useUiT();
  if (count <= 0) return null;
  return (
    <Collapsible.Root defaultOpen={defaultOpen} variant="section" className={className}>
      <Collapsible.Trigger>
        <Collapsible.Icon />
        {t("trash.removed", { count })}
      </Collapsible.Trigger>
      <Collapsible.Panel>
        <ul className="grid gap-2" aria-label={t("trash.removed", { count })}>{children}</ul>
      </Collapsible.Panel>
    </Collapsible.Root>
  );
}

export interface RemovedItemProps {
  label: React.ReactNode;
  trashedByLabel?: string | null;
  trashReason?: string | null;
  /** Present only when the reader may restore; absent hides the control. */
  onRestore?: () => void;
  busy?: boolean;
  children?: React.ReactNode;
}

/** One removed record: its name, who removed it and why, and Restore when allowed. */
export function RemovedItem({
  label,
  trashedByLabel,
  trashReason,
  onRestore,
  busy = false,
  children,
}: RemovedItemProps): React.ReactElement {
  const t = useUiT();
  return (
    <li className="flex items-start justify-between gap-3 rounded-6 border border-border-subtle px-3 py-2">
      <div className="grid min-w-0 gap-0.5">
        <span className="truncate text-13 font-medium text-fg">{label}</span>
        {children}
        {trashedByLabel ? (
          <span className="text-xs text-fg-muted">{t("trash.removedBy", { name: trashedByLabel })}</span>
        ) : null}
        {trashReason ? (
          <span className="text-xs text-fg-muted">{t("trash.removedReason", { reason: trashReason })}</span>
        ) : null}
      </div>
      {onRestore ? (
        <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onRestore}>
          {t("trash.restore")}
        </Button>
      ) : null}
    </li>
  );
}

export interface RemovedRecordsProps<TRow extends Row = Row> {
  resource: DataResourceMetadata | null | undefined;
  /** The record's display name. */
  label: (row: TRow) => string;
  /** Fields the label reads, beside the identity, permissions and trash stamps. */
  fields?: readonly string[];
  /** Narrow removed records to the surface's scope, e.g. one container. */
  filters?: readonly CrudFilter[];
  /** Keep only rows the surface lists as removed, e.g. the roots of a removed subtree. */
  select?: (rows: readonly TRow[]) => readonly TRow[];
  pageSize?: number;
  className?: string;
}

type RemovedRecordRow = BaseRecord & Row;

/**
 * "Removed (n)" for any trashable resource: the trashed rows the reader may
 * still read (Zed withholds them from everyone else), each with Restore when
 * the row projects `delete`. Renders nothing for an untrashable resource.
 */
export function RemovedRecords<TRow extends Row = Row>({
  resource,
  label,
  fields = [],
  filters = [],
  select,
  pageSize = 50,
  className,
}: RemovedRecordsProps<TRow>): React.ReactElement | null {
  const t = useUiT();
  const flag = trashFlagField(resource);
  const { restore, busy } = useTrashRecord(resource);
  const selection = React.useMemo(
    () => refineFieldsFromPaths(["id", "permissions", ...TRASH_STAMP_FIELDS, ...fields]),
    [fields],
  );
  const list = useList<RemovedRecordRow, HttpError>({
    resource: refineResourceName(resource ?? null),
    dataProviderName: resource?.schemaName,
    filters: flag ? [{ field: flag, operator: "eq", value: true }, ...filters] : [...filters],
    pagination: { mode: "server", currentPage: 1, pageSize },
    meta: { fields: selection },
    queryOptions: { enabled: Boolean(resource && flag) },
  });
  const loaded = (list.result.data ?? []) as unknown as readonly TRow[];
  const rows = select ? select(loaded) : loaded;
  if (!flag) return null;
  if (list.query.error) return <ErrorBanner description={t("trash.loadFailed")} />;
  return (
    <RemovedDisclosure count={select ? rows.length : list.result.total ?? rows.length} className={className}>
      {rows.map((row) => {
        const id = rowPublicId(row);
        return (
          <RemovedItem
            key={id ?? label(row)}
            label={label(row)}
            trashedByLabel={typeof row.trashed_by_label === "string" ? row.trashed_by_label : null}
            trashReason={typeof row.trash_reason === "string" ? row.trash_reason : null}
            busy={busy}
            {...(id && holdsPermission(row, TRASH_PERMISSION) ? { onRestore: () => void restore(id) } : {})}
          />
        );
      })}
    </RemovedDisclosure>
  );
}
