import { rowPublicId, type Row } from "@angee/metadata";
import { type DocumentType } from "@angee/gql/console";
import { Button, Glyph, errorMessage, useToast } from "@angee/ui";
import * as React from "react";

import { ConnectIntegration } from "../documents";
import { useIntegrateT } from "../i18n";
import { INTEGRATION_CONNECTION_FIELDS } from "../IntegrationLifecycleActions";
import { connectCallbackRedirectUri } from "./redirects";
import { useOAuthAuthorization } from "./use-oauth-authorization";

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

export { parseManualCode } from "./use-oauth-authorization";
