import { useCallback } from "react";
import type { Row } from "@angee/metadata";
import { defineRowAction, useUiT, type ActionDescriptor, type RowActionDeclaration } from "@angee/ui";

/**
 * One daemon verb (start, sync base, fetch…) declared once and rendered by the
 * shared owners: `RecordActionBar` on a record page (through
 * {@link verbDescriptors}) and the list's row-action column (through
 * {@link verbRowActions}). Confirmation and failure toasts belong to those
 * owners; `perform` only fires the mutation and throws on failure.
 */
export interface DaemonVerb<TSubject extends object> {
  id: string;
  label: string;
  /** The page's one primary verb when it is visible. */
  primary?: boolean;
  /** Destructive: danger-toned and confirmed by the owner before it runs. */
  danger?: boolean;
  /** Where the record page shows it: a band button or the `⋮` menu. */
  placement?: "toolbar" | "menu";
  /** The confirmation copy; a danger verb without it is confirmed with the standard copy. */
  confirm?: { title: string; body: (subject: TSubject) => string };
  visible?: (subject: TSubject) => boolean;
  perform: (subject: TSubject) => Promise<void>;
}

/** The verbs as `RecordActionBar` descriptors: the band and menu of a daemon record page. */
export function verbDescriptors<TSubject extends object>(verbs: readonly DaemonVerb<TSubject>[]): readonly ActionDescriptor[] {
  return verbs.map((verb) => ({
    id: verb.id,
    label: verb.label,
    primary: verb.primary,
    danger: verb.danger,
    placement: verb.placement ?? "toolbar",
    visibleWhen: verb.visible ? (record) => verb.visible!(record as TSubject) : undefined,
    confirm: verb.confirm
      ? (record) => ({ title: verb.confirm!.title, body: verb.confirm!.body(record as TSubject), confirm: verb.label })
      : undefined,
    run: async (context) => {
      if (context.record) await verb.perform(context.record as TSubject);
    },
  }));
}

/** The verbs as the list's row actions, disabled together while any daemon mutation runs. */
export function verbRowActions<TRow extends Row>(
  verbs: readonly DaemonVerb<TRow>[],
  busy: boolean,
): readonly RowActionDeclaration<TRow>[] {
  return verbs.map((verb) => defineRowAction<TRow>({
    kind: "page",
    id: verb.id,
    label: verb.label,
    variant: verb.danger ? "danger" : verb.primary ? "secondary" : "ghost",
    visible: (row) => verb.visible?.(row) ?? true,
    disabled: () => busy,
    pendingPolicy: "active-row",
    confirm: verb.confirm
      ? { title: () => verb.confirm!.title, body: (row) => verb.confirm!.body(row), confirm: () => verb.label }
      : undefined,
    onSelect: verb.perform,
  }));
}

/**
 * Fire one daemon mutation for a verb. The daemon reports failure as a GraphQL
 * error (the transport rejects), so any returned root field is success and a
 * missing root field is a failure, not a silent success; either way the snapshot
 * is refetched. Errors propagate to the owner (`RecordActionBar`, the row action),
 * which toasts them with the verb's label.
 */
export function useDaemonMutationRun(refetch: () => void): <V extends Record<string, unknown>>(params: {
  run: (variables: V) => Promise<object | undefined>;
  field: string;
  variables: V;
}) => Promise<void> {
  const t = useUiT();
  return useCallback(async ({ run, field, variables }) => {
    try {
      const data = await run(variables);
      if (data == null || (data as Record<string, unknown>)[field] == null) {
        throw new Error(t("action.noResult"));
      }
    } finally {
      refetch();
    }
  }, [refetch, t]);
}
