import * as React from "react";
import type { Row } from "@angee/metadata";
import type {
  AuthoredDocument,
  AuthoredVariables,
  DocumentData,
} from "@angee/refine";

import { errorMessage, useConfirm, useToast } from "../../feedback";
import { Glyph } from "../../chrome/Glyph";
import { Button, type ButtonVariant } from "../../ui/button";
import { useAuthoredResourceMutation } from "./authored-resource-mutation";

export interface RowActionConfirmCopy<TRow extends Row> {
  title: (row: TRow) => React.ReactNode;
  body: (row: TRow) => React.ReactNode;
  confirm: (row: TRow) => React.ReactNode;
  cancel?: (row: TRow) => React.ReactNode;
}

export interface RowActionToastCopy<TRow extends Row> {
  title: (row: TRow) => React.ReactNode;
  description: (row: TRow) => string;
}

/**
 * Whether an accepted action shows a spinner on its initiating row or keeps the
 * whole action set quietly disabled. Both policies prevent a second row action
 * until the active verb settles.
 */
export type RowActionPendingPolicy = "active-row" | "disable-actions";

const rowActionDeclarationBrand: unique symbol = Symbol("rowActionDeclaration");

interface RowActionDeclarationBase<TRow extends Row> {
  readonly [rowActionDeclarationBrand]: true;
  id: string;
  label: string;
  icon?: string;
  variant: ButtonVariant;
  visible: (row: TRow) => boolean;
  disabled: (row: TRow) => boolean;
  pendingPolicy: RowActionPendingPolicy;
}

/** An authored mutation rendered and run by the shared row-action surface. */
export interface AuthoredRowActionDeclaration<TRow extends Row>
  extends RowActionDeclarationBase<TRow> {
  kind: "authored";
  document: AuthoredDocument;
  variables: (row: TRow) => unknown;
  succeeded: (result: unknown) => boolean;
  invalidateModels: readonly string[];
  confirm?: RowActionConfirmCopy<TRow>;
  toast: RowActionToastCopy<TRow>;
}

/** A page-owned verb rendered alongside authored mutations in the same column. */
export interface PageRowActionDeclaration<TRow extends Row>
  extends RowActionDeclarationBase<TRow> {
  kind: "page";
  onSelect: (row: TRow) => void | Promise<void>;
}

/** Opaque, helper-authored declaration consumed by ListView/RowsListView. */
export type RowActionDeclaration<TRow extends Row> =
  | AuthoredRowActionDeclaration<TRow>
  | PageRowActionDeclaration<TRow>;

export interface TypedAuthoredRowActionDeclaration<
  TRow extends Row,
  TDocument extends AuthoredDocument,
> extends Omit<
    AuthoredRowActionDeclaration<TRow>,
    | typeof rowActionDeclarationBrand
    | "document"
    | "variables"
    | "succeeded"
    | "visible"
    | "disabled"
  > {
  document: TDocument;
  variables: (row: TRow) => AuthoredVariables<TDocument>;
  succeeded?: (result: DocumentData<TDocument> | undefined) => boolean;
  visible?: (row: TRow) => boolean;
  disabled?: (row: TRow) => boolean;
}

export interface TypedPageRowActionDeclaration<TRow extends Row>
  extends Omit<
    PageRowActionDeclaration<TRow>,
    typeof rowActionDeclarationBrand | "visible" | "disabled"
  > {
  visible?: (row: TRow) => boolean;
  disabled?: (row: TRow) => boolean;
}

const alwaysVisible = (): boolean => true;
const alwaysEnabled = (): boolean => false;

/** The shared variables projector for the common `$id` authored mutation. */
export function rowIdVariables<TRow extends { id: string }>(row: TRow): {
  id: string;
} {
  return { id: row.id };
}

export function defineRowAction<
  TRow extends Row,
  TDocument extends AuthoredDocument,
>(
  declaration: TypedAuthoredRowActionDeclaration<TRow, TDocument>,
): AuthoredRowActionDeclaration<TRow>;
export function defineRowAction<TRow extends Row>(
  declaration: TypedPageRowActionDeclaration<TRow>,
): PageRowActionDeclaration<TRow>;
/**
 * Brand a row-action declaration while preserving an authored document's
 * result/variables types. Authored success defaults to the truthiness of the
 * document's sole root field, so a nullable no-op root is a failure.
 */
export function defineRowAction<
  TRow extends Row,
  TDocument extends AuthoredDocument,
>(
  declaration:
    | TypedAuthoredRowActionDeclaration<TRow, TDocument>
    | TypedPageRowActionDeclaration<TRow>,
): RowActionDeclaration<TRow> {
  const visible = declaration.visible ?? alwaysVisible;
  const disabled = declaration.disabled ?? alwaysEnabled;
  if (declaration.kind === "page") {
    return {
      ...declaration,
      [rowActionDeclarationBrand]: true,
      visible,
      disabled,
    };
  }
  const succeeded = declaration.succeeded;
  return {
    ...declaration,
    [rowActionDeclarationBrand]: true,
    visible,
    disabled,
    variables: (row) => declaration.variables(row),
    succeeded: succeeded
      ? (result) => succeeded(result as DocumentData<TDocument> | undefined)
      : singleRootSucceeded,
  };
}

function singleRootSucceeded(result: unknown): boolean {
  if (!result || typeof result !== "object" || Array.isArray(result)) return false;
  const roots = Object.values(result);
  return roots.length === 1 && Boolean(roots[0]);
}

interface PendingRowAction<TRow extends Row> {
  actionId: string;
  row: TRow;
  phase: "armed" | "running";
}

export interface RowActionsController<TRow extends Row> {
  pending: PendingRowAction<TRow> | null;
  arm: (actionId: string, row: TRow) => boolean;
  commit: (actionId: string, row: TRow) => void;
  release: (actionId: string, row: TRow) => void;
}

export interface RowActionsSurface<TRow extends Row> {
  hasActions: boolean;
  render: (row: TRow) => React.ReactNode;
}

/** One pending owner shared by every rendered row in a list surface. */
export function useRowActionsController<TRow extends Row>(): RowActionsController<TRow> {
  const [pending, setPending] = React.useState<PendingRowAction<TRow> | null>(null);
  const pendingRef = React.useRef<PendingRowAction<TRow> | null>(null);
  const arm = React.useCallback((actionId: string, row: TRow): boolean => {
    if (pendingRef.current) return false;
    const next: PendingRowAction<TRow> = { actionId, row, phase: "armed" };
    pendingRef.current = next;
    setPending(next);
    return true;
  }, []);
  const commit = React.useCallback((actionId: string, row: TRow): void => {
    const current = pendingRef.current;
    if (current?.actionId !== actionId || current.row !== row) return;
    const next: PendingRowAction<TRow> = { actionId, row, phase: "running" };
    pendingRef.current = next;
    setPending(next);
  }, []);
  const release = React.useCallback((actionId: string, row: TRow): void => {
    const current = pendingRef.current;
    if (current?.actionId !== actionId || current.row !== row) return;
    pendingRef.current = null;
    setPending(null);
  }, []);
  return React.useMemo(
    () => ({ pending, arm, commit, release }),
    [arm, commit, pending, release],
  );
}

/** Stable row renderer shared by ListView and RowsListView. */
export function useRowActionsSurface<TRow extends Row>(
  actions: readonly RowActionDeclaration<TRow>[] | undefined,
): RowActionsSurface<TRow> {
  const controller = useRowActionsController<TRow>();
  const render = React.useCallback((row: TRow) => {
    const visibleActions = actions?.filter((action) => action.visible(row));
    if (!visibleActions || visibleActions.length === 0) return null;
    return (
      <VisibleRowActions
        actions={visibleActions}
        controller={controller}
        row={row}
      />
    );
  }, [actions, controller]);
  return React.useMemo(
    () => ({ hasActions: (actions?.length ?? 0) > 0, render }),
    [actions?.length, render],
  );
}

export interface DeclaredRowActionsProps<TRow extends Row> {
  actions: readonly RowActionDeclaration<TRow>[];
  controller: RowActionsController<TRow>;
  row: TRow;
}

/** Render every visible declared verb for one row through one busy owner. */
export function DeclaredRowActions<TRow extends Row>({
  actions,
  controller,
  row,
}: DeclaredRowActionsProps<TRow>): React.ReactElement | null {
  const visibleActions = actions.filter((action) => action.visible(row));
  if (visibleActions.length === 0) return null;
  return (
    <VisibleRowActions
      actions={visibleActions}
      controller={controller}
      row={row}
    />
  );
}

/** Paint a visibility-resolved action set without evaluating its predicates again. */
function VisibleRowActions<TRow extends Row>({
  actions,
  controller,
  row,
}: DeclaredRowActionsProps<TRow>): React.ReactElement {
  return (
    <div className="flex justify-end gap-1">
      {actions.map((action) =>
        action.kind === "authored" ? (
          <AuthoredRowActionButton
            key={action.id}
            action={action}
            controller={controller}
            row={row}
          />
        ) : (
          <PageRowActionButton
            key={action.id}
            action={action}
            controller={controller}
            row={row}
          />
        ),
      )}
    </div>
  );
}

interface AuthoredRowActionButtonProps<TRow extends Row> {
  action: AuthoredRowActionDeclaration<TRow>;
  controller: RowActionsController<TRow>;
  row: TRow;
}

function AuthoredRowActionButton<TRow extends Row>({
  action,
  controller,
  row,
}: AuthoredRowActionButtonProps<TRow>): React.ReactElement {
  const requestConfirm = useConfirm();
  const toast = useToast();
  const mutationOptions = React.useMemo(
    () => ({
      invalidateModels: action.invalidateModels,
      shouldInvalidate: (result: unknown) => action.succeeded(result),
    }),
    [action],
  );
  const [mutate] = useAuthoredResourceMutation(action.document, mutationOptions);
  const active =
    controller.pending?.actionId === action.id
    && controller.pending.row === row
    && controller.pending.phase === "running";
  const busy = controller.pending !== null;
  const runArmed = React.useCallback(async (): Promise<void> => {
    try {
      if (action.confirm) {
        const accepted = await requestConfirm({
          title: action.confirm.title(row),
          body: action.confirm.body(row),
          confirm: action.confirm.confirm(row),
          ...(action.confirm.cancel
            ? { cancel: action.confirm.cancel(row) }
            : {}),
          danger: true,
        });
        if (!accepted) return;
      }
      controller.commit(action.id, row);
      // The unexported declaration brand proves this document and projector
      // were paired by defineRowAction before the heterogeneous array erased TDocument.
      const result = await mutate(action.variables(row) as never);
      if (!action.succeeded(result)) {
        throw new Error(action.toast.description(row));
      }
    } catch (cause) {
      toast.danger({
        title: action.toast.title(row),
        description: errorMessage(cause, action.toast.description(row)),
      });
    } finally {
      controller.release(action.id, row);
    }
  }, [action, controller, mutate, requestConfirm, row, toast]);

  return (
    <RowActionButton
      action={action}
      active={active}
      busy={busy}
      row={row}
      onSelect={() => {
        if (!controller.arm(action.id, row)) return;
        void runArmed();
      }}
    />
  );
}

interface PageRowActionButtonProps<TRow extends Row> {
  action: PageRowActionDeclaration<TRow>;
  controller: RowActionsController<TRow>;
  row: TRow;
}

function PageRowActionButton<TRow extends Row>({
  action,
  controller,
  row,
}: PageRowActionButtonProps<TRow>): React.ReactElement {
  const active =
    controller.pending?.actionId === action.id
    && controller.pending.row === row
    && controller.pending.phase === "running";
  const busy = controller.pending !== null;
  const runArmed = React.useCallback(async (): Promise<void> => {
    try {
      controller.commit(action.id, row);
      await action.onSelect(row);
    } finally {
      controller.release(action.id, row);
    }
  }, [action, controller, row]);
  return (
    <RowActionButton
      action={action}
      active={active}
      busy={busy}
      row={row}
      onSelect={() => {
        if (!controller.arm(action.id, row)) return;
        void runArmed();
      }}
    />
  );
}

interface RowActionButtonProps<TRow extends Row> {
  action: RowActionDeclaration<TRow>;
  active: boolean;
  busy: boolean;
  row: TRow;
  onSelect: () => void;
}

function RowActionButton<TRow extends Row>({
  action,
  active,
  busy,
  row,
  onSelect,
}: RowActionButtonProps<TRow>): React.ReactElement {
  return (
    <Button
      type="button"
      variant={action.variant}
      size={action.icon ? "iconSm" : "sm"}
      aria-label={action.label}
      title={action.icon ? action.label : undefined}
      disabled={busy || action.disabled(row)}
      pending={active && action.pendingPolicy === "active-row"}
      onClick={(event) => {
        event.stopPropagation();
        if (action.disabled(row)) return;
        onSelect();
      }}
    >
      {action.icon ? <Glyph decorative name={action.icon} /> : action.label}
    </Button>
  );
}
