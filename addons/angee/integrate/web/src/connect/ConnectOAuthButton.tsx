import { rowPublicId, useResourceInvalidates, useSchemaFieldMetadata, type Row } from "@angee/metadata";
import { useAuthoredMutation } from "@angee/refine";
import { type DocumentType } from "@angee/gql/console";
import { Button, Glyph, errorMessage, usePrompt, useToast } from "@angee/ui";
import * as React from "react";

import { ConnectIntegration } from "../documents";
import { useIntegrateT } from "../i18n";
import { INTEGRATION_CONNECTION_FIELDS } from "../IntegrationLifecycleActions";
import { IntegrateConnectAccountComplete } from "./documents.public";
import { connectCallbackRedirectUri } from "./redirects";

export type OAuthConnectPayload = NonNullable<
  DocumentType<typeof ConnectIntegration>["connect_integration"]
>;

type RedirectInput = { redirectUri: string; next: string };
type StartResult = Promise<OAuthConnectPayload | null | undefined>;
type ConnectLabels = {
  next: string;
  label?: string;
  connectedTitle?: string;
  startErrorTitle?: string;
  onConnected?: () => void;
  disabled?: boolean;
};

/** Existing callers own selection; record callers opt into server-projected readiness. */
export type ConnectOAuthButtonProps = ConnectLabels & (
  | { row: Row; start: (input: RedirectInput & { id: string }) => StartResult }
  | { row?: undefined; start: (input: RedirectInput) => StartResult }
);

/** Compose the shared prompt, mutation and toast owners for browser authorization. */
export function ConnectOAuthButton(props: ConnectOAuthButtonProps): React.ReactElement | null {
  const t = useIntegrateT();
  const prompt = usePrompt();
  const toast = useToast();
  const metadata = useSchemaFieldMetadata();
  const models = React.useMemo(() => ["integrate.Integration", "integrate.Credential", "integrate.ExternalAccount",
    ...(metadata.resources ?? []).filter((resource) => resource.canonicalLabel === "integrate.Integration").map((resource) => resource.modelLabel),
  ], [metadata]);
  const invalidates = useResourceInvalidates(models);
  const [complete, completion] = useAuthoredMutation(IntegrateConnectAccountComplete, { invalidateModels: models, invalidates });
  const [starting, setStarting] = React.useState(false);
  const label = props.label ?? t(props.row?.is_reconnect_required === true ? "connect.reconnect" : "connect.action");
  const successTitle = props.connectedTitle ?? t("connect.connected");
  const failureTitle = props.startErrorTitle ?? t("connect.startError");

  if (props.row && (!rowPublicId(props.row) || props.row.can_connect !== true)) return null;

  async function authorize(): Promise<boolean> {
    const redirect = { redirectUri: connectCallbackRedirectUri(), next: props.next };
    const result = props.row
      ? await props.start({ ...redirect, id: rowPublicId(props.row)! })
      : await props.start(redirect);
    if (result?.error) throw new Error(result.error);
    if (result?.attached) return true;
    if (!result?.authorize_url) throw new Error(failureTitle);
    if (result.mode !== "manual") {
      window.location.assign(result.authorize_url);
      return false;
    }

    const response = await prompt({
      title: label,
      body: <span>
        <a href={result.authorize_url} target="_blank" rel="noreferrer" className="underline">
          {t("providers.connect.openAuthorize")}
        </a>
        {t("providers.connect.instructions")}
      </span>,
      fields: [{
        name: "pasted",
        label: t("providers.connect.codeLabel"),
        placeholder: t("providers.connect.codePlaceholder"),
      }],
    });
    if (!response) return false;
    if (!result.redirect_uri) throw new Error(t("providers.connect.stateIncomplete"));
    const grant = parseManualCode(response.pasted, result.state ?? "", t);
    const completed = await complete({ ...grant, redirectUri: result.redirect_uri });
    const account = completed?.connect_account_complete;
    if (!account) throw new Error(t("providers.connect.stateIncomplete"));
    if (account.error) throw new Error(account.error);
    return true;
  }

  async function connect(): Promise<void> {
    setStarting(true);
    try {
      if (await authorize()) {
        props.onConnected?.();
        toast.success({ title: successTitle });
      }
    } catch (error) {
      toast.danger({ title: label, description: errorMessage(error, failureTitle) });
    } finally {
      setStarting(false);
    }
  }

  return <Button type="button" size="sm" variant="primary"
    disabled={props.disabled} loading={starting || completion.fetching} onClick={() => { void connect(); }}>
    <Glyph name="link" />{label}
  </Button>;
}

/** Public facts used by record-based connect and lifecycle actions. */
export const CONNECT_RECORD_FIELDS = [...INTEGRATION_CONNECTION_FIELDS, "can_connect"] as const;

export function parseManualCode(
  value: unknown, expectedState: string, t: (key: string) => string,
): { code: string; state: string } {
  const pasted = String(value ?? "").trim();
  const separator = pasted.lastIndexOf("#");
  if (separator <= 0 || separator === pasted.length - 1) {
    throw new Error(t("providers.connect.codeIncomplete"));
  }
  const code = pasted.slice(0, separator);
  const state = pasted.slice(separator + 1);
  if (expectedState && state !== expectedState) throw new Error(t("providers.connect.codeMismatch"));
  return { code, state };
}
