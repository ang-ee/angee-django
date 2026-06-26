import * as React from "react";
import { Button, Glyph, errorMessage, usePrompt, useToast, useAuthoredMutation } from "@angee/ui";
import { type DocumentType } from "@angee/gql/console";

import { useIntegrateT } from "../i18n";
import { ConnectIntegration } from "../documents";
import { IntegrateConnectAccountComplete } from "./documents.public";
import { connectCallbackRedirectUri } from "./redirects";

/** The OAuth connect-start payload, from the generated `connect_integration` result. */
export type OAuthConnectPayload = NonNullable<
  DocumentType<typeof ConnectIntegration>["connect_integration"]
>;

export interface ConnectOAuthButtonProps {
  label: string;
  connectedTitle: string;
  startErrorTitle: string;
  next: string;
  start: (input: {
    redirectUri: string;
    next: string;
  }) => Promise<OAuthConnectPayload | null | undefined>;
  onConnected: () => void;
}

/** Button-owned browser flow for outbound OAuth connect, including manual code mode. */
export function ConnectOAuthButton({
  label,
  connectedTitle,
  startErrorTitle,
  next,
  start,
  onConnected,
}: ConnectOAuthButtonProps): React.ReactElement {
  const t = useIntegrateT();
  const prompt = usePrompt();
  const toast = useToast();
  const [connectAccountComplete, completeState] = useAuthoredMutation(
    IntegrateConnectAccountComplete,
  );
  const [starting, setStarting] = React.useState(false);

  const connect = async (): Promise<void> => {
    setStarting(true);
    try {
      const payload = await start({
        redirectUri: connectCallbackRedirectUri(),
        next,
      });
      if (payload?.error) throw new Error(payload.error);
      if (payload?.attached) {
        onConnected();
        toast.success({ title: connectedTitle });
        return;
      }
      if (!payload?.authorize_url) {
        throw new Error(startErrorTitle);
      }
      if (payload.mode !== "manual") {
        window.location.assign(payload.authorize_url);
        return;
      }
      const entered = await prompt({
        title: label,
        body: (
          <span>
            <a
              href={payload.authorize_url}
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
      if (!payload.redirect_uri) {
        throw new Error(t("integrate.providers.connect.stateIncomplete"));
      }
      const completed = await connectAccountComplete({
        code,
        state,
        redirectUri: payload.redirect_uri,
      });
      const done = completed?.connect_account_complete;
      if (done?.error) throw new Error(done.error);
      onConnected();
      toast.success({ title: connectedTitle });
    } finally {
      setStarting(false);
    }
  };

  return (
    <Button
      type="button"
      size="sm"
      variant="primary"
      loading={starting || completeState.fetching}
      onClick={() => {
        void connect().catch((error) => {
          toast.danger({
            title: label,
            description: errorMessage(error, startErrorTitle),
          });
        });
      }}
    >
      <Glyph name="link" />
      {label}
    </Button>
  );
}

export function canConnectRecord(row: Record<string, unknown>): boolean {
  return row.credential === null || normalizeValue(row.status) === "draft";
}

function normalizeValue(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

export function parseManualCode(
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
