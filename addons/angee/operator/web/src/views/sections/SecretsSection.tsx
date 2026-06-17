import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  FieldLabel,
  FieldRoot,
  Input,
  RowsListView,
  useConfirm,
  useToast,
  type ListColumn,
} from "@angee/base";
import { useCallback, useId, useMemo, useState, type FormEvent, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { SECRET_DELETE_MUTATION, SECRET_SET_MUTATION } from "../../data/documents";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { SecretRef } from "../../data/types";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface SecretSetVars extends Record<string, unknown> {
  name: string;
  value: string;
}
interface SecretDeleteVars extends Record<string, unknown> {
  name: string;
}

// RowsListView keys rows by `id`; the daemon identifies a secret by name.
type SecretRowData = SecretRef & { id: string };

/** Secrets pane: declared secrets (presence only) + set/delete. */
export function SecretsSection(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ secrets: true });
  const { setSecret, deleteSecret, busy } = useSecretActions(refetch);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const nameId = useId();
  const valueId = useId();

  const canSet = name.trim().length > 0 && value.length > 0 && !busy;

  const rows = useMemo<readonly SecretRowData[]>(
    () => (snapshot?.secrets ?? []).map((secret) => ({ ...secret, id: secret.name })),
    [snapshot],
  );

  async function submitSet(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!canSet) return;
    const succeeded = await setSecret(name.trim(), value);
    // Keep the value on failure so the operator can retry without re-typing it.
    if (succeeded) {
      setValue("");
    }
  }

  const columns = useMemo<readonly ListColumn<SecretRowData>[]>(
    () => [
      {
        field: "name",
        header: t("operator.secrets.column.name"),
        render: (secret) => <span className="font-medium text-fg">{secret.name}</span>,
      },
      {
        field: "declared",
        header: t("operator.secrets.column.declared"),
        render: (secret) => (
          <span className="text-13 text-fg-muted">
            {secret.declared ? t("operator.secrets.yes") : t("operator.secrets.no")}
          </span>
        ),
      },
      {
        field: "hasValue",
        header: t("operator.secrets.column.hasValue"),
        render: (secret) => (
          <Badge density="compact" shape="pill" tone={secret.hasValue ? "success" : "neutral"}>
            {secret.hasValue ? t("operator.secrets.value.set") : t("operator.secrets.value.empty")}
          </Badge>
        ),
      },
      {
        field: "required",
        header: t("operator.secrets.column.required"),
        render: (secret) =>
          secret.required ? (
            <Badge density="compact" shape="pill" tone="warning">
              {t("operator.secrets.yes")}
            </Badge>
          ) : (
            <span className="text-fg-muted">—</span>
          ),
      },
      {
        field: "envVar",
        header: t("operator.secrets.column.envVar"),
        render: (secret) => (
          <span className="font-mono text-13 text-fg-muted">{secret.envVar ?? "—"}</span>
        ),
      },
      {
        field: "actions",
        header: t("operator.table.actions"),
        sortable: false,
        align: "right",
        render: (secret) =>
          secret.required || secret.generated ? (
            // Required/generated secrets are control-plane (e.g. the
            // generated operator bearer shared by Django + the daemon);
            // deleting one can brick minting, so the console withholds it.
            <span
              className="text-13 text-fg-muted"
              title={t("operator.secrets.protected.hint")}
            >
              {t("operator.secrets.protected")}
            </span>
          ) : (
            <Button
              disabled={busy}
              onClick={() => deleteSecret(secret)}
              size="sm"
              variant="ghost"
            >
              {t("operator.secrets.delete")}
            </Button>
          ),
      },
    ],
    [busy, deleteSecret, t],
  );

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>{t("operator.secrets.form.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => void submitSet(event)}>
            <FieldRoot>
              <FieldLabel htmlFor={nameId} className="text-fg-muted">
                {t("operator.secrets.form.name")}
              </FieldLabel>
              <Input
                id={nameId}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("operator.secrets.form.namePlaceholder")}
                value={name}
              />
            </FieldRoot>
            <FieldRoot>
              <FieldLabel htmlFor={valueId} className="text-fg-muted">
                {t("operator.secrets.form.value")}
              </FieldLabel>
              <Input
                id={valueId}
                onChange={(event) => setValue(event.target.value)}
                placeholder={t("operator.secrets.form.valuePlaceholder")}
                type="password"
                value={value}
              />
            </FieldRoot>
            <Button disabled={!canSet} size="sm" type="submit" variant="secondary">
              {t("operator.secrets.form.submit")}
            </Button>
          </form>
        </CardContent>
      </Card>

      <RowsListView<SecretRowData>
        rows={rows}
        columns={columns}
        fetching={result.fetching}
        error={snapshot ? null : result.error}
        emptyMessage={t("operator.secrets.empty")}
      />
    </div>
  );
}

/**
 * The two secret mutations — set (form-driven) and delete (per-row, confirmed)
 * — each run via {@link runDaemonAction} and surface a failure as a toast; the
 * live snapshot then reflects the new state, so neither needs a local result
 * store. `setSecret` returns whether it succeeded so the form can clear the
 * value only on success.
 */
function useSecretActions(refetch: () => void): {
  setSecret: (name: string, value: string) => Promise<boolean>;
  deleteSecret: (secret: SecretRef) => void;
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const toast = useToast();

  const set = useOperatorAction<DaemonActionData, SecretSetVars>(SECRET_SET_MUTATION);
  const remove = useOperatorAction<DaemonActionData, SecretDeleteVars>(SECRET_DELETE_MUTATION);
  const busy = set.result.fetching || remove.result.fetching;

  const setError = useCallback(
    (message: string | null) => {
      if (message) toast.danger({ title: message });
    },
    [toast],
  );

  const setSecret = useCallback(
    (name: string, value: string): Promise<boolean> =>
      runDaemonAction({
        run: set.run,
        field: "secretSet",
        variables: { name, value },
        label: t("operator.secrets.set.label"),
        setError,
        refetch,
      }),
    [refetch, set.run, setError, t],
  );

  const deleteSecret = useCallback(
    (secret: SecretRef): void => {
      void (async () => {
        const ok = await confirm({
          title: t("operator.secrets.delete.confirm.title"),
          body: t("operator.secrets.delete.confirm.body", { name: secret.name }),
          confirm: t("operator.secrets.delete"),
          danger: true,
        });
        if (!ok) return;
        await runDaemonAction({
          run: remove.run,
          field: "secretDelete",
          variables: { name: secret.name },
          label: t("operator.secrets.delete.label"),
          setError,
          refetch,
        });
      })();
    },
    [confirm, refetch, remove.run, setError, t],
  );

  return { setSecret, deleteSecret, busy };
}
