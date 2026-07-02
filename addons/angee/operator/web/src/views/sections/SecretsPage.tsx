import { Badge, Button, cn, RowsListView, textRoleVariants, useConfirm, usePrompt, type ListColumn } from "@angee/ui";
import { useCallback, useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import {
  SECRET_DELETE_MUTATION,
  SECRET_SET_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorAction } from "../../data/transport";
import type { SecretRef } from "../../data/types";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { useOperatorRows } from "../parts/operator-rows";
import { useRunDaemonAction } from "../parts/run-action";

type SecretRowData = DaemonRow<SecretRef>;

/** Secrets page: declared secrets (presence only) + set (via a prompt) / delete. */
export function SecretsPage(): ReactNode {
  const t = useOperatorT();
  const prompt = usePrompt();
  const { rows, fetching, error, refetch } = useOperatorRows(
    { secrets: true },
    (snapshot) => daemonRowsByName(snapshot.secrets),
  );
  const { setSecret, deleteSecret, busy } = useSecretActions(refetch);

  // The set form is a prompt (a form surface), not a panel crammed above the list.
  // A row's name pre-fills it; the toolbar action collects an arbitrary name.
  const promptSet = useCallback(
    (presetName?: string): void => {
      void (async () => {
        const values = await prompt({
          title: t("secrets.form.title"),
          confirm: t("secrets.form.submit"),
          fields: [
            {
              name: "name",
              label: t("secrets.form.name"),
              placeholder: t("secrets.form.namePlaceholder"),
              defaultValue: presetName,
              readOnly: presetName !== undefined,
            },
            {
              name: "value",
              label: t("secrets.form.value"),
              placeholder: t("secrets.form.valuePlaceholder"),
              type: "password",
            },
          ],
        });
        if (!values) return;
        const name = (values.name ?? "").trim();
        const value = values.value ?? "";
        if (name.length === 0 || value.length === 0) return;
        await setSecret(name, value);
      })();
    },
    [prompt, setSecret, t],
  );

  const columns = useMemo<readonly ListColumn<SecretRowData>[]>(
    () => [
      {
        field: "name",
        header: t("secrets.column.name"),
        render: (secret) => <span className="font-medium text-fg">{secret.name}</span>,
      },
      {
        field: "declared",
        header: t("secrets.column.declared"),
        render: (secret) => (
          <span className={textRoleVariants({ role: "meta" })}>
            {secret.declared ? t("secrets.yes") : t("secrets.no")}
          </span>
        ),
      },
      {
        field: "hasValue",
        header: t("secrets.column.hasValue"),
        render: (secret) => (
          <Badge density="compact" shape="pill" tone={secret.hasValue ? "success" : "neutral"}>
            {secret.hasValue ? t("secrets.value.set") : t("secrets.value.empty")}
          </Badge>
        ),
      },
      {
        field: "required",
        header: t("secrets.column.required"),
        render: (secret) =>
          secret.required ? (
            <Badge density="compact" shape="pill" tone="warning">
              {t("secrets.yes")}
            </Badge>
          ) : (
            <span className="text-fg-muted">—</span>
          ),
      },
      {
        field: "envVar",
        header: t("secrets.column.envVar"),
        render: (secret) => (
          <span className={textRoleVariants({ role: "meta", mono: true })}>{secret.envVar ?? "—"}</span>
        ),
      },
      {
        field: "actions",
        header: t("table.actions"),
        sortable: false,
        align: "right",
        render: (secret) =>
          secret.required || secret.generated ? (
            // Required/generated secrets are control-plane (e.g. the generated
            // operator bearer shared by Django + the daemon); deleting one can
            // brick minting, so the console withholds it but still allows a re-set.
            <div className="flex justify-end gap-1">
              <Button disabled={busy} onClick={() => promptSet(secret.name)} size="sm" variant="ghost">
                {t("secrets.form.submit")}
              </Button>
              <span className={cn(textRoleVariants({ role: "meta" }), "self-center")} title={t("secrets.protected.hint")}>
                {t("secrets.protected")}
              </span>
            </div>
          ) : (
            <div className="flex justify-end gap-1">
              <Button disabled={busy} onClick={() => promptSet(secret.name)} size="sm" variant="ghost">
                {t("secrets.form.submit")}
              </Button>
              <Button disabled={busy} onClick={() => deleteSecret(secret)} size="sm" variant="ghost">
                {t("secrets.delete")}
              </Button>
            </div>
          ),
      },
    ],
    [busy, deleteSecret, promptSet, t],
  );

  return (
    <RowsListView<SecretRowData>
      rows={rows}
      columns={columns}
      toolbarActions={
        <Button disabled={busy} onClick={() => promptSet()} size="sm" variant="secondary">
          {t("secrets.form.title")}
        </Button>
      }
      fetching={fetching}
      error={error}
      emptyContent={t("secrets.empty")}
    />
  );
}

/** Secret mutations: prompt-driven set plus per-row confirmed delete. */
function useSecretActions(refetch: () => void): {
  setSecret: (name: string, value: string) => Promise<boolean>;
  deleteSecret: (secret: SecretRef) => void;
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const runDaemon = useRunDaemonAction(refetch);

  const set = useOperatorAction(SECRET_SET_MUTATION);
  const remove = useOperatorAction(SECRET_DELETE_MUTATION);
  const busy = set.result.fetching || remove.result.fetching;

  const setSecret = useCallback(
    (name: string, value: string): Promise<boolean> =>
      runDaemon({
        run: set.run,
        field: "insert_secrets_one",
        variables: { object: { name, value } },
        label: t("secrets.set.label"),
      }),
    [runDaemon, set.run, t],
  );

  const deleteSecret = useCallback(
    (secret: SecretRef): void => {
      void (async () => {
        const ok = await confirm({
          title: t("secrets.delete.confirm.title"),
          body: t("secrets.delete.confirm.body", { name: secret.name }),
          confirm: t("secrets.delete"),
          danger: true,
        });
        if (!ok) return;
        await runDaemon({
          run: remove.run,
          field: "delete_secrets_by_pk",
          variables: { id: secret.id },
          label: t("secrets.delete.label"),
        });
      })();
    },
    [confirm, remove.run, runDaemon, t],
  );

  return { setSecret, deleteSecret, busy };
}
