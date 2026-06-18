import * as React from "react";
import {
  Button,
  Column,
  DataPage,
  Field,
  Form,
  Glyph,
  Group,
  GroupListView,
  List,
  useEnumOptions,
  useImplPrefill,
  usePrompt,
  useToast,
  type DataToolbarGroupOption,
} from "@angee/base";
import { useAuthoredMutation, type Row } from "@angee/sdk";

import {
  CONNECT_ACCOUNT_COMPLETE_MUTATION,
  type ConnectAccountCompleteData,
  type ConnectAccountCompleteVariables,
} from "../connect/documents";
import { connectCallbackRedirectUri } from "../connect/redirects";
import {
  CONNECT_INTEGRATION_MUTATION,
  type ConnectIntegrationData,
  type ConnectIntegrationVariables,
} from "../documents";
import { useIntegrateT } from "../i18n";

const MODEL = "integrate.Integration";
const CONNECT_NEXT = "/integrate";

export function IntegrationsPage(): React.ReactElement {
  const t = useIntegrateT();
  const implClassOptions = useEnumOptions(MODEL, "implClass");
  const implClassPrefill = useImplPrefill(MODEL, "implClass");

  // Aggregate on the real groupable axes (impl_class / vendor / status); the
  // capability lane displays the impl's category. Tone is resolved by the shared
  // STATUS_TONES vocabulary — never a private map (docs/frontend/guidelines.md).
  const groupOptions = React.useMemo<readonly DataToolbarGroupOption[]>(
    () => [
      {
        id: "impl-category",
        label: t("integrate.col.capability"),
        group: {
          field: "implCategory",
          aggregateField: "implClass",
          aggregateKey: "implClass",
        },
      },
      {
        id: "vendor",
        label: t("integrate.col.vendor"),
        group: {
          field: "vendorLabel",
          aggregateField: "vendor",
          aggregateKey: "vendorId",
        },
      },
      { id: "status", label: t("integrate.col.status"), group: { field: "status" } },
    ],
    [t],
  );

  const cardActions = React.useCallback(
    (row: Row, context: { refresh: () => void }) =>
      canConnectIntegration(row) ? (
        <IntegrationConnectButton row={row} refresh={context.refresh} />
      ) : null,
    [],
  );

  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      cardActions={cardActions}
    >
      <List
        model={MODEL}
        list={GroupListView}
        groupOptions={groupOptions}
        defaultGroups={{
          list: {
            field: "implCategory",
            aggregateField: "implClass",
            aggregateKey: "implClass",
          },
          board: {
            field: "implCategory",
            aggregateField: "implClass",
            aggregateKey: "implClass",
          },
        }}
      >
        <Column field="displayName" />
        <Column field="implLabel" header={t("integrate.col.capability")} />
        <Column field="vendorLabel" header={t("integrate.col.vendor")} />
        <Column field="status" widget="statusBadge" />
        <Column
          field="credential.displayName"
          header={t("integrate.col.credential")}
        />
        <Column field="lastError" header={t("integrate.col.lastError")} />
      </List>
      <Form model={MODEL} layout="tabs">
        <Field name="displayName" title readOnly />
        <Group label={t("integrate.integrations.identity")} columns={2}>
          <Field name="owner" createOnly />
          <Field name="vendor" createOnly />
          <Field
            name="implClass"
            label={t("integrate.integrations.implClass")}
            widget="select"
            options={implClassOptions}
            prefill={implClassPrefill}
            createOnly
          />
          <Field name="status" widget="statusbar" editOnly />
        </Group>
        <Group label={t("integrate.integrations.inference")} columns={2}>
          <Field
            name="name"
            label={t("integrate.integrations.providerName")}
            createOnly
            showWhen={(values) =>
              isCreateForm(values) && isInferenceImpl(values.implClass)
            }
          />
          <Field
            name="baseUrl"
            label={t("integrate.integrations.baseUrl")}
            createOnly
            showWhen={(values) =>
              isCreateForm(values) && isInferenceImpl(values.implClass)
            }
          />
          <Field
            name="companionConfig"
            label={t("integrate.integrations.providerConfig")}
            widget="json"
            createOnly
            showWhen={(values) =>
              isCreateForm(values) && isInferenceImpl(values.implClass)
            }
          />
        </Group>
        <Group label={t("integrate.integrations.vcs")} columns={2}>
          <Field
            name="webhookSecret"
            label={t("integrate.integrations.webhookSecret")}
            widget="text"
            kind="string"
            createOnly
            showWhen={(values) =>
              isCreateForm(values) && isVcsImpl(values.implClass)
            }
          />
        </Group>
        <Group label={t("integrate.integrations.authentication")} columns={2}>
          <Field name="credential" editOnly />
          <Field name="account" editOnly />
        </Group>
        <Group label={t("integrate.integrations.runtime")} columns={2}>
          <Field name="config" widget="json" />
          <Field name="lastUsedAt" readOnly />
          <Field name="lastUsedStatus" readOnly />
          <Field name="useCount24h" readOnly />
          <Field name="errorCount24h" readOnly />
          <Field name="lastError" readOnly />
        </Group>
      </Form>
    </DataPage>
  );
}

function IntegrationConnectButton({
  row,
  refresh,
}: {
  row: Row;
  refresh: () => void;
}): React.ReactElement | null {
  const t = useIntegrateT();
  const prompt = usePrompt();
  const toast = useToast();
  const [connectIntegration, connectState] = useAuthoredMutation<
    ConnectIntegrationData,
    ConnectIntegrationVariables
  >(CONNECT_INTEGRATION_MUTATION);
  const [connectAccountComplete, completeState] = useAuthoredMutation<
    ConnectAccountCompleteData,
    ConnectAccountCompleteVariables
  >(CONNECT_ACCOUNT_COMPLETE_MUTATION);
  const id = typeof row.id === "string" ? row.id : "";
  if (!id) return null;

  const connect = async (): Promise<void> => {
    const result = await connectIntegration({
      integrationId: id,
      redirectUri: connectCallbackRedirectUri(),
      next: CONNECT_NEXT,
    });
    const payload = result?.connectIntegration;
    if (payload?.error) throw new Error(payload.error);
    if (payload?.attached) {
      refresh();
      toast.success({ title: t("integrate.integrations.connect.connected") });
      return;
    }
    if (!payload?.authorizeUrl) {
      throw new Error(t("integrate.integrations.connect.startError"));
    }
    if (payload.mode !== "manual") {
      window.location.assign(payload.authorizeUrl);
      return;
    }
    const entered = await prompt({
      title: t("integrate.integrations.action.connect"),
      body: (
        <span>
          <a
            href={payload.authorizeUrl}
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            {t("integrate.providers.connect.openAuthorize")}
          </a>
          {t("integrate.providers.connect.instructions")}
        </span>
      ),
      fields: [
        {
          name: "pasted",
          label: t("integrate.providers.connect.codeLabel"),
          placeholder: t("integrate.providers.connect.codePlaceholder"),
        },
      ],
    });
    if (!entered) return;
    const { code, state } = parseManualCode(
      entered.pasted,
      payload.state ?? "",
      t,
    );
    if (!payload.redirectUri) {
      throw new Error(t("integrate.providers.connect.stateIncomplete"));
    }
    const completed = await connectAccountComplete({
      code,
      state,
      redirectUri: payload.redirectUri,
    });
    const done = completed?.connectAccountComplete;
    if (done?.error) throw new Error(done.error);
    refresh();
    toast.success({ title: t("integrate.integrations.connect.connected") });
  };

  return (
    <Button
      type="button"
      size="sm"
      variant="primary"
      loading={connectState.fetching || completeState.fetching}
      onClick={() => {
        void connect().catch((error) => {
          toast.danger({
            title: t("integrate.integrations.action.connect"),
            description:
              error instanceof Error
                ? error.message
                : t("integrate.integrations.connect.startError"),
          });
        });
      }}
    >
      <Glyph name="link" />
      {t("integrate.integrations.action.connect")}
    </Button>
  );
}

function canConnectIntegration(row: Row): boolean {
  return row.credential == null || normalizeValue(row.status) === "draft";
}

function isCreateForm(values: Row): boolean {
  return normalizeValue(values.status) === "";
}

// These mirror the impl keys registered under each category in
// ANGEE_INTEGRATION_IMPLS. The server owns impl->category; until that category
// is projected to the create form, keep these in sync with the registry.
function isVcsImpl(value: unknown): boolean {
  return ["github", "local"].includes(normalizeValue(value));
}

function isInferenceImpl(value: unknown): boolean {
  return ["anthropic", "openai", "manual"].includes(normalizeValue(value));
}

function normalizeValue(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

function parseManualCode(
  pastedValue: unknown,
  expectedState: string,
  t: (key: string) => string,
): { code: string; state: string } {
  const pasted = String(pastedValue ?? "").trim();
  const hash = pasted.lastIndexOf("#");
  const code = hash > 0 ? pasted.slice(0, hash) : "";
  const state = hash > 0 ? pasted.slice(hash + 1) : "";
  if (!code || !state) {
    throw new Error(t("integrate.providers.connect.codeIncomplete"));
  }
  if (expectedState && state !== expectedState) {
    throw new Error(t("integrate.providers.connect.codeMismatch"));
  }
  return { code, state };
}
