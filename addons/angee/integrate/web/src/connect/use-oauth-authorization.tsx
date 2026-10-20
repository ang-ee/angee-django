import { useAuthoredMutation } from "@angee/refine";
import { useResourceInvalidates, useSchemaFieldMetadata } from "@angee/metadata";
import { NavLink, usePrompt } from "@angee/ui";
import * as React from "react";

import { useIntegrateT } from "../i18n";
import { IntegrateConnectAccountComplete } from "./documents.public";

/** The facts a connect-start mutation returns that the browser authorization needs. */
export interface OAuthAuthorizationStart {
  authorize_url?: string | null;
  mode?: string | null;
  state?: string | null;
  redirect_uri?: string | null;
}

export interface OAuthAuthorization {
  /**
   * Finish one authorization the server started: a redirect-mode provider takes
   * the browser away (resolves `false`; the callback page completes), a manual
   * provider shows its code in another tab and the user pastes it here (resolves
   * `true` once the completion mutation accepts it, `false` when cancelled).
   */
  authorize: (start: OAuthAuthorizationStart, title: React.ReactNode) => Promise<boolean>;
  /** The completion mutation is in flight. */
  completing: boolean;
  /** Every model a completed connection can change. */
  models: readonly string[];
}

/**
 * The one browser-authorization ceremony for outbound OAuth: the redirect or
 * manual code-paste flow and its completion mutation. Record connect buttons and
 * the providers' Connect account verb compose it; neither spells the prompt again.
 */
export function useOAuthAuthorization(): OAuthAuthorization {
  const t = useIntegrateT();
  const prompt = usePrompt();
  const metadata = useSchemaFieldMetadata();
  const models = React.useMemo(() => ["integrate.Integration", "integrate.Credential", "integrate.ExternalAccount",
    ...(metadata.resources ?? []).filter((resource) => resource.canonicalLabel === "integrate.Integration").map((resource) => resource.modelLabel),
  ], [metadata]);
  const invalidates = useResourceInvalidates(models);
  const [complete, completion] = useAuthoredMutation(IntegrateConnectAccountComplete, { invalidateModels: models, invalidates });

  const authorize = React.useCallback(async (start: OAuthAuthorizationStart, title: React.ReactNode): Promise<boolean> => {
    if (!start.authorize_url) throw new Error(t("providers.connect.startError"));
    if (start.mode !== "manual") {
      window.location.assign(start.authorize_url);
      return false;
    }
    // The provider only displays the code (no redirect back), so this tab stays alive
    // and the user pastes the `code#state` it shows. The link is a real user gesture,
    // so it is never popup-blocked.
    const response = await prompt({
      title,
      body: <span>
        <NavLink href={start.authorize_url} target="_blank" rel="noreferrer" variant="inline">
          {t("providers.connect.openAuthorize")}
        </NavLink>
        {t("providers.connect.instructions")}
      </span>,
      fields: [{
        name: "pasted",
        label: t("providers.connect.codeLabel"),
        placeholder: t("providers.connect.codePlaceholder"),
      }],
    });
    if (!response) return false;
    if (!start.redirect_uri) throw new Error(t("providers.connect.stateIncomplete"));
    const grant = parseManualCode(response.pasted, start.state ?? "", t);
    const completed = await complete({ ...grant, redirectUri: start.redirect_uri });
    const account = completed?.connect_account_complete;
    if (!account) throw new Error(t("providers.connect.stateIncomplete"));
    if (account.error) throw new Error(account.error);
    return true;
  }, [complete, prompt, t]);

  return { authorize, completing: completion.fetching, models };
}

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
