import * as React from "react";
import {
  useList,
  type BaseRecord,
  type CrudFilter,
  type HttpError,
} from "@refinedev/core";
import { actionOutcomeReasons, extractActionOutcome, refineFieldsFromPaths, stableSerialize } from "@angee/refine";
import {
  allowsRecordPermission,
  refineResourceName,
  rowPublicId,
  resourceLifecycle,
  trashFlagField,
  type DataResourceMetadata,
  type Row,
} from "@angee/metadata";

import { usePrompt } from "../../feedback";
import { Glyph } from "../../chrome/Glyph";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";
import { Collapsible } from "../../ui/collapsible";
import { Pager } from "../../ui/pager";
import { SelectionBar } from "../../ui/selection-bar";
import type { ActionDescriptor } from "../page/Action";
import { useActionResultRun } from "./action-result-run";
import { useAuthoredResourceMutation } from "./authored-resource-mutation";
import { RestoreRecord, TrashRecord } from "./documents";
import { BULK_ACTION_FANOUT_LIMIT, type BulkSelectionProps } from "./bulk-action";

/** The record permission that offers trash and restore — the shared verbs' authority.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export const TRASH_PERMISSION = "delete";

/** Mirrors the backend's `TRASH_REASON_MAX_LENGTH`.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export const TRASH_REASON_MAX_LENGTH = 1000;

/** The removed-record stamps the backend's `TrashedRefMixin` projects.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export const TRASH_STAMP_FIELDS = ["trashed_at", "trash_reason", "trashed_by_label"] as const;

/** Ask to move one record to the trash; resolves the optional reason, or `null` when kept. */
export type TrashPrompt = (name?: string) => Promise<{ reason: string } | null>;

/**
 * The one trash confirmation: a danger dialog with an optional reason and a
 * cancel label that names the record when the caller knows its name, or counts
 * the records when the caller supplies a bulk count. Domain
 * verbs with their own transport (record chatter) compose it before their
 * mutation; resource records use {@link useTrashRecord}.
 * @owner ModalsHost
 */
export function useTrashPrompt(count?: number): TrashPrompt {
  const prompt = usePrompt();
  const t = useUiT();
  return React.useCallback<TrashPrompt>(async (name) => {
    const values = await prompt({
      title: count !== undefined ? t("trash.bulkTitle", { count }) : name ? t("trash.titleNamed", { name }) : t("trash.title"),
      body: t("trash.body"),
      fields: [{
        name: "reason",
        label: t("trash.reason"),
        type: "textarea",
        placeholder: t("trash.reasonPlaceholder"),
        maxLength: TRASH_REASON_MAX_LENGTH,
      }],
      confirm: t("trash.confirm"),
      cancel: count !== undefined ? t("trash.bulkCancel", { count }) : name ? t("trash.cancelNamed", { name }) : t("trash.cancel"),
      danger: true,
    });
    return values ? { reason: (values.reason ?? "").trim() } : null;
  }, [count, prompt, t]);
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

function trashTargetType(resource: DataResourceMetadata | null | undefined): string | null {
  return resourceLifecycle(resource).trashFlag ? resource?.resourceType ?? null : null;
}

/**
 * Run the shared `trash_record` / `restore_record` verbs for one trashable
 * resource, settling their outcome through the shared toast owner and
 * refreshing the resource's caches. The server checks `delete` on the record.
 * @owner DeletePreviewDialog
 */
export function useTrashRecord(resource: DataResourceMetadata | null | undefined): UseTrashRecordResult {
  const askTrash = useTrashPrompt();
  const settle = useActionResultRun();
  const targetType = trashTargetType(resource);
  const options = targetType && resource
    ? { dataProviderName: resource.schemaName, invalidateModels: [resource.modelLabel] }
    : null;
  const [trashRecord, trashState] = useAuthoredResourceMutation(TrashRecord, options ? {
    ...options,
    errorNotification: false,
    shouldInvalidate: (data) => data?.trash_record.ok === true,
  } : {});
  const [restoreRecord, restoreState] = useAuthoredResourceMutation(RestoreRecord, options ? {
    ...options,
    errorNotification: false,
    shouldInvalidate: (data) => data?.restore_record.ok === true,
  } : {});
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

/** Generic selection verbs over the same trashable resource contract as the record.
 * @owner SelectionBar
 */
export function BulkTrashActions(props: BulkSelectionProps): React.ReactElement | null {
  const targetType = trashTargetType(props.modelMetadata.resource);
  const flag = resourceLifecycle(props.modelMetadata.resource).trashFlag;
  return targetType && flag ? <TrashSelectionActions {...props} targetType={targetType} flag={flag} /> : null;
}

function TrashSelectionActions({ modelMetadata, selectedRows, unprocessedCount, bulk, targetType, flag }: BulkSelectionProps & {
  targetType: string;
  flag: string;
}): React.ReactElement {
  const t = useUiT();
  const resource = modelMetadata.resource;
  // List rows do not select `permissions`: the shared verb checks `delete` per row
  // and a refused row is reported in the outcome.
  const selections = [false, true].map((restore) => ({
    restore,
    ids: selectedRows.flatMap((row) => {
      const id = rowPublicId(row);
      return id && row[flag] === restore ? [id] : [];
    }),
  }));
  const askTrash = useTrashPrompt(Math.min(selections[0]!.ids.length, BULK_ACTION_FANOUT_LIMIT));
  // No per-row cache invalidation or toast: the bulk owner settles the whole run.
  const options = { dataProviderName: resource.schemaName, errorNotification: false as const };
  const [trashRecord] = useAuthoredResourceMutation(TrashRecord, options);
  const [restoreRecord] = useAuthoredResourceMutation(RestoreRecord, options);
  return <>{selections.map(({ restore, ids }) => {
    if (!ids.length) return null;
    return <SelectionBar.Action key={String(restore)} surface="brand" pending={bulk.pending} disabled={bulk.blocked} {...bulk.reason}
      onClick={() => {
        let reason = "";
        void bulk.run({
          ids, unprocessedCount,
          confirm: restore ? undefined : async () => {
            const answer = await askTrash();
            if (!answer) return false;
            reason = answer.reason;
            return true;
          },
          perform: async (id) => {
            const target = { target_type: targetType, target_id: id };
            const outcome = extractActionOutcome(
              restore ? await restoreRecord(target) : await trashRecord({ ...target, reason }),
              restore ? "restore_record" : "trash_record",
            );
            if (!outcome) throw new Error(t("action.noResult"));
            if (!outcome.ok) {
              return { status: "refused", reason: actionOutcomeReasons(outcome) || outcome.message || t("action.noResult") };
            }
          },
          done: (count) => t(restore ? "trash.bulkRestored" : "trash.bulkDone", { count }),
          failed: (count) => t(restore ? "trash.bulkRestoreFailed" : "trash.bulkFailed", { count }),
        });
      }}>
      <Glyph name={restore ? "undo-2" : "trash"} />{t(restore ? "trash.restore" : "trash.action")}
    </SelectionBar.Action>;
  })}</>;
}

export interface TrashActionOptions<TRow extends Row = Row> {
  /** The record's display name in the confirmation copy. */
  name?: (record: TRow) => string;
  /** Called only after the server accepted moving this record to the trash. */
  onTrashed?: () => void;
}

/**
 * Record-form verbs for a trashable resource: Trash on an untrashed record,
 * Restore on a trashed one, requiring `delete` when the model declares
 * permissions. The form supplies these menu verbs and selects the trash flag.
 * @owner RecordActionBar
 */
export function useTrashActions(
  resource: DataResourceMetadata | null | undefined,
  options: TrashActionOptions = {},
): readonly ActionDescriptor[] {
  const t = useUiT();
  const { available, trash, restore } = useTrashRecord(resource);
  const flag = resourceLifecycle(resource).trashFlag;
  const { name, onTrashed } = options;
  return React.useMemo<readonly ActionDescriptor[]>(() => {
    if (!available || flag === null) return [];
    return [
      {
        id: "trash",
        label: t("trash.action"),
        icon: "trash",
        placement: "menu",
        visibleWhen: (record) => allowsRecordPermission(record, TRASH_PERMISSION, resource) && record[flag] === false,
        run: async (context) => {
          const id = rowPublicId(context.record);
          if (id && context.record && await trash(id, name?.(context.record))) {
            context.refresh();
            onTrashed?.();
          }
        },
      },
      {
        id: "restore",
        label: t("trash.restore"),
        icon: "undo-2",
        placement: "menu",
        visibleWhen: (record) => allowsRecordPermission(record, TRASH_PERMISSION, resource) && record[flag] === true,
        run: async (context) => {
          const id = rowPublicId(context.record);
          if (id && await restore(id)) context.refresh();
        },
      },
    ];
  }, [available, resource, flag, name, onTrashed, restore, t, trash]);
}

export interface RemovedDisclosureProps {
  /** Removed records the reader manages; nothing renders at zero. */
  count: number;
  defaultOpen?: boolean;
  className?: string;
  children: React.ReactNode;
}

/** The collapsed "Removed (n)" section shared by every removed-records list.
 * @kind fragment
 */
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

/** One removed record: its name, who removed it and why, and Restore when allowed.
 * @kind fragment
 */
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
          <span className="text-12 text-fg-muted">{t("trash.removedBy", { name: trashedByLabel })}</span>
        ) : null}
        {trashReason ? (
          <span className="text-12 text-fg-muted">{t("trash.removedReason", { reason: trashReason })}</span>
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

export interface RemovedListProps<TRow extends Row = Row> {
  /** The model's permission contract; domain reads may supply their own canRestore. */
  resource?: DataResourceMetadata | null;
  pagination?: { page: number; pageSize: number; onPageChange: (page: number) => void };
  /** The removed rows, from the shared trash read or a domain read (a queue's removed tasks). */
  rows: readonly TRow[];
  /** The removed count when the read pages; defaults to the rows given. */
  total?: number;
  /** The read's failure. */
  error?: unknown;
  /** The record's display name. */
  label: (row: TRow) => string;
  /** Where a row keeps who removed it and why; default: the shared trash stamps. */
  stamps?: (row: TRow) => { trashedByLabel?: string | null; trashReason?: string | null };
  /** Override the resource's permission contract for a domain-owned restore verb. */
  canRestore?: (row: TRow) => boolean;
  /** The restore verb: the shared `restore_record`, or a domain's own. */
  restore?: (row: TRow) => void | Promise<unknown>;
  /** Marks every Restore busy while a restore runs. */
  busy?: boolean;
  className?: string;
}

/**
 * "Removed (n)" over rows already read: the disclosure, each row's name, who removed
 * it and why, and Restore when the reader may. `RemovedRecords` composes it over the
 * shared trash read and verbs; a domain with its own removed read and restore verb
 * composes it directly.
 * @kind surface
 */
export function RemovedList<TRow extends Row = Row>({
  resource,
  pagination,
  rows,
  total,
  error,
  label,
  stamps,
  canRestore,
  restore,
  busy = false,
  className,
}: RemovedListProps<TRow>): React.ReactElement | null {
  const t = useUiT();
  if (error) return <ErrorBanner description={t("trash.loadFailed")} />;
  return (
    <RemovedDisclosure count={total ?? rows.length} className={className}>
      {rows.map((row) => {
        const id = rowPublicId(row);
        const stamp = stamps?.(row) ?? {
          trashedByLabel: typeof row.trashed_by_label === "string" ? row.trashed_by_label : null,
          trashReason: typeof row.trash_reason === "string" ? row.trash_reason : null,
        };
        const restorable = restore && (canRestore ? canRestore(row) : Boolean(id) && Boolean(resource) && allowsRecordPermission(row, TRASH_PERMISSION, resource));
        return (
          <RemovedItem
            key={id ?? label(row)}
            label={label(row)}
            trashedByLabel={stamp.trashedByLabel}
            trashReason={stamp.trashReason}
            busy={busy}
            {...(restorable ? { onRestore: () => void restore(row) } : {})}
          />
        );
      })}
      {pagination && (total ?? rows.length) > pagination.pageSize
        ? <li><Pager {...pagination} total={total} /></li> : null}
    </RemovedDisclosure>
  );
}

export interface RemovedRecordsProps<TRow extends Row = Row> extends Pick<RemovedListProps<TRow>, "label" | "className"> {
  resource: DataResourceMetadata | null | undefined;
  /** Fields the label reads, beside the identity, permissions and trash stamps. */
  fields?: readonly string[];
  /** Narrow removed records to the surface's scope, e.g. one container. */
  filters?: readonly CrudFilter[];
  pageSize?: number;
}

type RemovedRecordRow = BaseRecord & Row;

/**
 * "Removed (n)" for any trashable resource: the trashed rows the reader may
 * still read (Zed withholds them from everyone else), each with Restore when
 * the model's permission contract allows `delete`. Renders nothing for an untrashable resource.
 * @kind surface
 */
export function RemovedRecords<TRow extends Row = Row>({
  resource,
  label,
  fields = [],
  filters = [],
  pageSize = 50,
  className,
}: RemovedRecordsProps<TRow>): React.ReactElement | null {
  const flag = trashFlagField(resource);
  const declaresPermissions = !allowsRecordPermission(null, TRASH_PERMISSION, resource);
  const { restore, busy } = useTrashRecord(resource);
  const scope = stableSerialize([resource?.modelLabel, filters, pageSize]);
  const [pagination, setPagination] = React.useState({ scope, page: 1 });
  const page = pagination.scope === scope ? pagination.page : 1;
  const selection = React.useMemo(
    () => refineFieldsFromPaths(["id", ...(declaresPermissions ? ["permissions"] : []), ...TRASH_STAMP_FIELDS, ...fields]),
    [declaresPermissions, fields],
  );
  const list = useList<RemovedRecordRow, HttpError>({
    resource: refineResourceName(resource ?? null),
    dataProviderName: resource?.schemaName,
    filters: flag ? [{ field: flag, operator: "eq", value: true }, ...filters] : [...filters],
    pagination: { mode: "server", currentPage: page, pageSize },
    meta: { fields: selection },
    queryOptions: { enabled: Boolean(resource && flag) },
  });
  React.useEffect(() => {
    if (list.query.isFetching || list.result.total === undefined) return;
    const last = Math.max(1, Math.ceil(list.result.total / pageSize));
    if (page > last) setPagination({ scope, page: last });
  }, [list.query.isFetching, list.result.total, page, pageSize, scope]);
  const rows = (list.result.data ?? []) as unknown as readonly TRow[];
  if (!flag) return null;
  return (
    <RemovedList
      resource={resource}
      pagination={{ page, pageSize, onPageChange: (next) => setPagination({ scope, page: next }) }}
      rows={rows}
      total={list.result.total ?? rows.length}
      error={list.query.error}
      label={label}
      busy={busy}
      restore={(row) => { const id = rowPublicId(row); if (id) void restore(id); }}
      className={className}
    />
  );
}
