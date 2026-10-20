import { Button, Code, Glyph, MutationDialog, RowsListView, StatusBadge, defineRowAction, mutationDialogValueCodecs, type DescriptorField, type ListColumn, type RowActionDeclaration } from "@angee/ui";
import { useCallback, useMemo, useState, type ReactElement, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { SECRET_DELETE_MUTATION, SECRET_SET_MUTATION } from "../../data/documents.daemon";
import { useOperatorAction } from "../../data/transport";
import type { SecretRef } from "../../data/types";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { useDaemonMutationRun } from "../parts/daemon-verbs";
import { useOperatorRows } from "../parts/operator-rows";

type SecretRowData = DaemonRow<SecretRef>;

/** Secrets page: declared secrets (presence only) + set (a form dialog) / delete. */
export function SecretsPage(): ReactNode {
  const t = useOperatorT();
  const { rows, fetching, error, refetch } = useOperatorRows(
    { secrets: true },
    (snapshot) => daemonRowsByName(snapshot.secrets),
  );
  const { setSecret, deleteSecret, busy } = useSecretActions(refetch);
  // The set form opens from the toolbar for any name, or from a row for that row's name.
  const [setTarget, setSetTarget] = useState<{ name?: string } | null>(null);

  const rowActions = useMemo<readonly RowActionDeclaration<SecretRowData>[]>(
    () => [
      defineRowAction({
        kind: "page", id: "set-secret", label: t("secrets.set.label"), variant: "ghost",
        disabled: () => busy, pendingPolicy: "active-row",
        onSelect: (secret) => { setSetTarget({ name: secret.name }); },
      }),
      defineRowAction({
        kind: "page", id: "delete-secret", label: t("secrets.delete"), variant: "danger",
        visible: (secret) => !secret.required && !secret.generated,
        disabled: () => busy, pendingPolicy: "active-row",
        confirm: {
          title: () => t("secrets.delete.confirm.title"),
          body: (secret) => t("secrets.delete.confirm.body", { name: secret.name }),
          confirm: () => t("secrets.delete"),
        },
        onSelect: deleteSecret,
      }),
    ],
    [busy, deleteSecret, t],
  );

  const columns = useMemo<readonly ListColumn<SecretRowData>[]>(
    () => [
      { field: "name", header: t("secrets.column.name") },
      { field: "declared", header: t("secrets.column.declared"), widget: "booleanBadge" },
      { field: "hasValue", header: t("secrets.column.hasValue"), render: (secret) => <StatusBadge value={secret.hasValue ? "set" : "empty"} /> },
      {
        field: "required", header: t("secrets.column.required"),
        render: (secret) => secret.required || secret.generated
          ? <StatusBadge value="protected" title={t("secrets.protectedHint")}>{secret.required ? t("secrets.required") : t("secrets.generated")}</StatusBadge>
          : null,
      },
      { field: "envVar", header: t("secrets.column.envVar"), render: (secret) => secret.envVar ? <Code truncate>{secret.envVar}</Code> : null },
    ],
    [t],
  );

  return (
    <>
      <RowsListView<SecretRowData>
        rows={rows}
        columns={columns}
        rowActions={rowActions}
        toolbarActions={
          <Button type="button" variant="primary" size="sm" disabled={busy} onClick={() => setSetTarget({})}>
            <Glyph decorative name="plus" />
            {t("secrets.set.label")}
          </Button>
        }
        fetching={fetching}
        error={error}
        emptyContent={t("secrets.empty")}
      />
      <SecretSetDialog
        open={setTarget !== null}
        presetName={setTarget?.name}
        onOpenChange={(open) => { if (!open) setSetTarget(null); }}
        onSubmit={setSecret}
      />
    </>
  );
}

/** The set-secret form: a name (fixed when opened from a row) and a write-only value. */
function SecretSetDialog({ open, presetName, onOpenChange, onSubmit }: {
  open: boolean;
  presetName?: string;
  onOpenChange: (open: boolean) => void;
  onSubmit: (name: string, value: string) => Promise<void>;
}): ReactElement {
  const t = useOperatorT();
  const fields = useMemo<readonly DescriptorField[]>(() => [
    { name: "name", label: t("secrets.form.name"), placeholder: t("secrets.form.namePlaceholder"), required: true, readOnly: presetName !== undefined },
    { name: "value", label: t("secrets.form.value"), placeholder: t("secrets.form.valuePlaceholder"), widget: "password", required: true },
  ], [presetName, t]);
  return (
    <MutationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("secrets.form.title")}
      fields={fields}
      initialValues={{ name: presetName ?? "" }}
      submitLabel={t("secrets.form.submit")}
      errorFallback={t("secrets.set.failed")}
      parseValues={(values) => ({
        name: mutationDialogValueCodecs.requiredString(values.name, "name").trim(),
        value: mutationDialogValueCodecs.requiredString(values.value, "value"),
      })}
      onSubmit={async ({ name, value }) => {
        await onSubmit(name, value);
        return { status: "ok", data: name };
      }}
    />
  );
}

/** Secret mutations: set and delete, each refetching the snapshot. */
function useSecretActions(refetch: () => void): {
  setSecret: (name: string, value: string) => Promise<void>;
  deleteSecret: (secret: SecretRef) => Promise<void>;
  busy: boolean;
} {
  const runDaemon = useDaemonMutationRun(refetch);
  const set = useOperatorAction(SECRET_SET_MUTATION);
  const remove = useOperatorAction(SECRET_DELETE_MUTATION);
  const busy = set.result.fetching || remove.result.fetching;

  const setSecret = useCallback(
    (name: string, value: string) => runDaemon({ run: set.run, field: "insert_secrets_one", variables: { object: { name, value } } }),
    [runDaemon, set.run],
  );
  const deleteSecret = useCallback(
    (secret: SecretRef) => runDaemon({ run: remove.run, field: "delete_secrets_by_pk", variables: { id: secret.id } }),
    [remove.run, runDaemon],
  );
  return { setSecret, deleteSecret, busy };
}
