import { type DocumentType } from "@angee/gql/console";
import {
  useAuthoredQuery,
  type AuthoredDocument,
  type AuthoredMutateOptions,
  type AuthoredVariables,
  type DocumentData,
} from "@angee/refine";
import {
  ActionTrigger,
  Alert,
  InlineEmpty,
  MutationDialog,
  NavLink,
  PaneRow,
  PaneRows,
  mutationDialogValueCodecs,
  useRouteHref,
  useRuntimeAuth,
  type DescriptorField,
  type DescriptorFieldControlProps,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import { ConnectWithAccountFields } from "../documents";
import { useIntegrateT } from "../i18n";
import { ConnectAccountButton } from "./ConnectAccountButton";

/** What connecting with one of the session user's accounts asks for: a consumer query's `ConnectWithAccountFields`. */
export type ConnectWithAccountFacts = DocumentType<typeof ConnectWithAccountFields>;

/** The dialog value naming the chosen account's credential. */
export const CONNECT_WITH_ACCOUNT_FIELD = "credentialId";

/** The model whose rows a connect-with-account query lists: the session user's credentials. */
const CREDENTIAL_MODEL = "integrate.Credential";

/** The page an administrator sets up OAuth clients on. */
const OAUTH_PROVIDERS_ROUTE = "integrate.providers";

/** The role that may set up an OAuth client (`integrate/oauth_client`'s `admin`); the server still decides. */
const OAUTH_CLIENT_ADMIN_ROLE = "angee/role:admin";

/** A consumer's connect-facts query: its data, and the reload once another account connected in place. */
export interface ConnectWithAccountQuery<TDocument extends AuthoredDocument> {
  data: DocumentData<TDocument> | undefined;
  /** Reload the facts: pass it as {@link ConnectWithAccountFieldOptions.onConnected}. */
  onConnected: () => void;
}

/**
 * Read a consumer's connect facts (a query spreading `ConnectWithAccountFields`)
 * as the session user's accounts change: the query depends on their credentials,
 * so a connected or removed account reloads it, and `onConnected` reloads it at
 * once after a manual-code connect.
 */
export function useConnectWithAccountQuery<TDocument extends AuthoredDocument>(
  document: TDocument,
  variables?: AuthoredVariables<TDocument>,
): ConnectWithAccountQuery<TDocument> {
  const query = useAuthoredQuery(document, variables, { models: [CREDENTIAL_MODEL] });
  const refetch = query.refetch;
  const onConnected = React.useCallback(() => { void refetch(); }, [refetch]);
  return { data: query.data, onConnected };
}

export interface ConnectWithAccountFieldOptions {
  /** The consumer's connect facts (its `ConnectWithAccount` query); none while they load. */
  facts: ConnectWithAccountFacts | null | undefined;
  /** The consumer's route a redirect-back provider returns to once another account connected. */
  returnRoute: string;
  /** Called once another account connected in place (a manual-code provider), so the consumer reloads its facts. */
  onConnected?: () => void;
}

/**
 * The account field of a connect-with-account dialog: choose one of the session
 * user's accounts the consumer reads with (`Integration.connectable_credentials`),
 * or connect another through its OAuth client asking for its `oauth_scopes`
 * ({@link ConnectAccountButton}). A dialog with more fields composes it among
 * them; {@link ConnectWithAccountAction} is the dialog that asks for nothing else.
 */
export function useConnectWithAccountField({
  facts,
  returnRoute,
  onConnected,
}: ConnectWithAccountFieldOptions): DescriptorField {
  const t = useIntegrateT();
  const control = React.useCallback(
    (props: DescriptorFieldControlProps) => (
      <AccountPicker {...props} facts={facts} returnRoute={returnRoute} onConnected={onConnected} />
    ),
    [facts, returnRoute, onConnected],
  );
  return React.useMemo<DescriptorField>(
    () => ({
      name: CONNECT_WITH_ACCOUNT_FIELD,
      label: t("connectWithAccount.account"),
      required: true,
      controlLabelMode: "group",
      control,
    }),
    [control, t],
  );
}

export interface ConnectWithAccountActionProps<TResult> extends ConnectWithAccountFieldOptions {
  /** The toolbar button's label. */
  label: string;
  title: string;
  description?: string;
  /** Shown when the connect fails without a message of its own. */
  errorFallback?: string;
  /**
   * The consumer's authored connect mutation, as `useAuthoredMutation` returns it,
   * taking the chosen account's credential id: the dialog runs it and renders its
   * refusal itself, so a failure is reported once.
   */
  connect: (variables: ConnectWithAccountVariables, options: AuthoredMutateOptions) => Promise<TResult | undefined>;
}

/** The variables a connect-with-account mutation takes: the chosen account's credential. */
export type ConnectWithAccountVariables = { credentialId: string };

/**
 * A toolbar connect verb whose dialog asks only which account to connect with,
 * then runs the consumer's connect mutation with it. The consumer supplies its
 * facts, copy and mutation; the account choice and "Connect another account" are
 * this owner's.
 */
export function ConnectWithAccountAction<TResult>({
  label,
  title,
  description,
  errorFallback,
  connect,
  ...field
}: ConnectWithAccountActionProps<TResult>): React.ReactElement {
  const t = useIntegrateT();
  const accountField = useConnectWithAccountField(field);
  const fields = React.useMemo(() => [accountField], [accountField]);

  return (
    <MutationDialog
      trigger={<ActionTrigger glyph="plus">{label}</ActionTrigger>}
      title={title}
      description={description}
      fields={fields}
      submitLabel={t("connectWithAccount.submit")}
      submittingLabel={t("connectWithAccount.submitting")}
      errorFallback={errorFallback}
      parseValues={parseConnectWithAccountValues}
      mutation={connect}
    />
  );
}

function parseConnectWithAccountValues(values: MutationDialogValues): ConnectWithAccountVariables {
  return {
    credentialId: mutationDialogValueCodecs.requiredString(
      values[CONNECT_WITH_ACCOUNT_FIELD],
      CONNECT_WITH_ACCOUNT_FIELD,
    ),
  };
}

type AccountPickerProps = DescriptorFieldControlProps & ConnectWithAccountFieldOptions;

/**
 * The bare dialog control listing the accounts to choose from, and connecting another one.
 * While the capability has no ready OAuth client and the user holds no account it
 * reads with, nothing can be chosen or connected: the control says the connection
 * is not configured, and offers an administrator the OAuth providers page.
 */
function AccountPicker({
  facts,
  returnRoute,
  onConnected,
  readOnly,
  invalid,
  describedBy,
  labelledBy,
  value,
  onChange,
}: AccountPickerProps): React.ReactElement {
  const t = useIntegrateT();
  const routeHref = useRouteHref();
  const auth = useRuntimeAuth();
  const selected = mutationDialogValueCodecs.string(value) ?? "";
  const accounts = facts?.accounts ?? [];
  const oauthClientId = facts?.oauth_client ?? null;
  const unconfigured = facts != null && oauthClientId === null && accounts.length === 0;
  const providersHref = unconfigured && auth.hasRole(OAUTH_CLIENT_ADMIN_ROLE)
    ? routeHref.maybe(OAUTH_PROVIDERS_ROUTE)
    : undefined;

  return (
    <div
      role="group"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      aria-invalid={invalid || undefined}
      className="grid gap-3"
    >
      {unconfigured ? (
        <Alert tone="warning" title={t("connectWithAccount.unconfigured")}
          actions={providersHref ? (
            <NavLink href={providersHref} variant="inline">{t("connectWithAccount.configure")}</NavLink>
          ) : undefined} />
      ) : accounts.length === 0 ? (
        <InlineEmpty icon="user" label={t("connectWithAccount.empty")} />
      ) : (
        <PaneRows>
          {accounts.map((account) => (
            <li key={account.id}>
              <PaneRow icon="user" title={account.display_name} selected={account.id === selected}
                label={t("connectWithAccount.use")} verb={t("connectWithAccount.use")}
                onOpen={readOnly ? undefined : () => onChange(account.id)} />
            </li>
          ))}
        </PaneRows>
      )}
      {oauthClientId ? (
        <div>
          <ConnectAccountButton oauthClientId={oauthClientId} scopes={facts?.oauth_scopes ?? []}
            next={routeHref(returnRoute)} label={t("connectWithAccount.connect")}
            onConnected={onConnected} disabled={readOnly} />
        </div>
      ) : null}
    </div>
  );
}
