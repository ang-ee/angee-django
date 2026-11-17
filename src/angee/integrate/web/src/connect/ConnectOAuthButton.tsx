import { rowPublicId, useResourceInvalidates, type Row } from "@angee/metadata";
import { type DocumentType } from "@angee/gql/console";
import { useAuthoredMutation } from "@angee/refine";
import { Button, Glyph, errorMessage, useRecordChromeContext, useToast, type ContainerChild } from "@angee/ui";
import { useRouterState } from "@tanstack/react-router";
import * as React from "react";

import { ConnectIntegration } from "../documents";
import { useIntegrateT } from "../i18n";
import { INTEGRATION_CONNECTION_FIELDS, INTEGRATION_MODEL } from "../IntegrationLifecycleActions";
import { connectCallbackRedirectUri } from "./redirects";
import { useOAuthAuthorization, type OAuthAuthorizationStart } from "./use-oauth-authorization";

export type OAuthConnectPayload = NonNullable<
  DocumentType<typeof ConnectIntegration>["connect_integration"]
>;

/** What a connect start answers: the authorization facts, a refusal, or an integration attached without one. */
export type ConnectStartPayload = OAuthAuthorizationStart & { error?: string | null; attached?: boolean | null };

type RedirectInput = { redirectUri: string; next: string };
type StartResult = Promise<ConnectStartPayload | null | undefined>;
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

/** The record's Connect verb over the shared browser-authorization ceremony. */
export function ConnectOAuthButton(props: ConnectOAuthButtonProps): React.ReactElement | null {
  const t = useIntegrateT();
  const toast = useToast();
  const { authorize, completing } = useOAuthAuthorization();
  const [starting, setStarting] = React.useState(false);
  const label = props.label ?? t(props.row?.is_reconnect_required === true ? "connect.reconnect" : "connect.action");
  const successTitle = props.connectedTitle ?? t("connect.connected");
  const failureTitle = props.startErrorTitle ?? t("connect.startError");

  if (props.row && (!rowPublicId(props.row) || props.row.can_connect !== true)) return null;

  async function connect(): Promise<void> {
    setStarting(true);
    try {
      const redirect = { redirectUri: connectCallbackRedirectUri(), next: props.next };
      const result = props.row
        ? await props.start({ ...redirect, id: rowPublicId(props.row)! })
        : await props.start(redirect);
      if (result?.error) throw new Error(result.error);
      const connected = result?.attached ? true : await authorize(result ?? {}, label);
      if (connected) {
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
    disabled={props.disabled} loading={starting || completing} onClick={() => { void connect(); }}>
    <Glyph name="link" />{label}
  </Button>;
}

/** Public facts used by record-based connect and lifecycle actions. */
export const CONNECT_RECORD_FIELDS = [...INTEGRATION_CONNECTION_FIELDS, "can_connect"] as const;

/** The saved integration record's OAuth Connect verb; the server's `can_connect` decides whether it shows. */
function ConnectIntegrationAction(): React.ReactElement | null {
  const context = useRecordChromeContext();
  const here = useRouterState({ select: (state) => state.location.href });
  const models = React.useMemo(() => [context.resource, INTEGRATION_MODEL], [context.resource]);
  const invalidates = useResourceInvalidates(models);
  const [connect] = useAuthoredMutation(ConnectIntegration, { invalidateModels: models, invalidates });
  if (!context.record || context.formReadOnly) return null;
  return <ConnectOAuthButton row={context.record} disabled={context.actionsBlocked} next={here}
    start={async ({ id, redirectUri, next }) =>
      (await connect({ resource: context.resource, id, redirectUri, next }))?.connect_integration}
  />;
}

/**
 * The Connect child a vendor addon contributes at its own model's `#actions`
 * for an OAuth registration; the provider returns to the record's page.
 */
export function connectIntegrationAction(): ContainerChild<React.ReactElement> {
  return { sequence: 10, requiredFields: [...CONNECT_RECORD_FIELDS], content: <ConnectIntegrationAction /> };
}

export { parseManualCode } from "./use-oauth-authorization";
