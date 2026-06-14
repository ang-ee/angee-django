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
  useConfirm,
} from "@angee/base";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { SECRET_DELETE_MUTATION, SECRET_SET_MUTATION } from "../../data/documents";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { SecretRef } from "../../data/types";
import { DaemonResourceTable } from "../parts/DaemonResourceTable";
import { OperatorSection } from "../parts/OperatorSection";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface SecretSetVars extends Record<string, unknown> {
  name: string;
  value: string;
}
interface SecretDeleteVars extends Record<string, unknown> {
  name: string;
}

/** Secrets pane: declared secrets (presence only) + set/delete. */
export function SecretsSection(): ReactNode {
  const t = useOperatorT();
  const confirm = useConfirm();
  const { snapshot, result, refetch } = useOperatorSnapshot({ secrets: true });
  const [actionError, setActionError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const nameId = useId();
  const valueId = useId();

  const setSecret = useOperatorAction<DaemonActionData, SecretSetVars>(SECRET_SET_MUTATION);
  const deleteSecret = useOperatorAction<DaemonActionData, SecretDeleteVars>(SECRET_DELETE_MUTATION);
  const busy = setSecret.result.fetching || deleteSecret.result.fetching;

  const secrets = snapshot?.secrets ?? [];
  const canSet = name.trim().length > 0 && value.length > 0 && !busy;

  async function submitSet(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!canSet) return;
    const succeeded = await runDaemonAction({
      run: setSecret.run,
      field: "secretSet",
      variables: { name: name.trim(), value },
      label: t("operator.secrets.set.label"),
      setError: setActionError,
      refetch,
    });
    // Keep the value on failure so the operator can retry without re-typing it.
    if (succeeded) {
      setValue("");
    }
  }

  function handleDelete(secret: SecretRef): void {
    void (async () => {
      const ok = await confirm({
        title: t("operator.secrets.delete.confirm.title"),
        body: t("operator.secrets.delete.confirm.body", { name: secret.name }),
        confirm: t("operator.secrets.delete"),
        danger: true,
      });
      if (!ok) return;
      await runDaemonAction({
        run: deleteSecret.run,
        field: "secretDelete",
        variables: { name: secret.name },
        label: t("operator.secrets.delete.label"),
        setError: setActionError,
        refetch,
      });
    })();
  }

  return (
    <OperatorSection
      title={t("section.operator.secrets.title")}
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.secrets.loading")}
      actionError={actionError}
    >
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

      <DaemonResourceTable
        columns={[
          {
            header: t("operator.secrets.column.name"),
            cell: (secret) => <span className="font-medium text-fg">{secret.name}</span>,
          },
          {
            header: t("operator.secrets.column.declared"),
            cell: (secret) => (
              <span className="text-13 text-fg-muted">
                {secret.declared ? t("operator.secrets.yes") : t("operator.secrets.no")}
              </span>
            ),
          },
          {
            header: t("operator.secrets.column.hasValue"),
            cell: (secret) => (
              <Badge density="compact" shape="pill" tone={secret.hasValue ? "success" : "neutral"}>
                {secret.hasValue ? t("operator.secrets.value.set") : t("operator.secrets.value.empty")}
              </Badge>
            ),
          },
          {
            header: t("operator.secrets.column.required"),
            cell: (secret) =>
              secret.required ? (
                <Badge density="compact" shape="pill" tone="warning">
                  {t("operator.secrets.yes")}
                </Badge>
              ) : (
                <span className="text-fg-muted">—</span>
              ),
          },
          {
            header: t("operator.secrets.column.envVar"),
            cell: (secret) => (
              <span className="font-mono text-13 text-fg-muted">{secret.envVar ?? "—"}</span>
            ),
          },
          {
            header: t("operator.secrets.column.actions"),
            align: "end",
            cell: (secret) =>
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
                  onClick={() => handleDelete(secret)}
                  size="sm"
                  variant="ghost"
                >
                  {t("operator.secrets.delete")}
                </Button>
              ),
          },
        ]}
        emptyMessage={t("operator.secrets.empty")}
        rowKey={(secret) => secret.name}
        rows={secrets}
      />
    </OperatorSection>
  );
}
