import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { parse, type DocumentNode } from "graphql";
import {
  useCustomMutation,
  useGetIdentity,
  useInvalidateAuthStore,
  useLogin,
  useLogout as useRefineLogout,
  type AuthActionResponse,
  type AuthProvider as RefineAuthProvider,
  type BaseRecord,
  type HttpError,
  type MetaQuery,
} from "@refinedev/core";

import {
  createAngeeGraphQLClient,
  type AngeeHasuraClientOptions,
} from "./provider";

export type UserPreferences = Record<string, unknown>;

export interface AuthUser {
  id: string;
  name: string;
  username?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
  isStaff?: boolean;
  isActive?: boolean;
  preferences?: UserPreferences;
  roles?: readonly string[];
}

export interface AuthState {
  user: AuthUser | null;
  status: "anonymous" | "authenticated";
  hasRole: (role: string) => boolean;
}

export interface CurrentUserPayload {
  id: string;
  username: string;
  firstName: string;
  lastName: string;
  email: string;
  isStaff: boolean;
  isActive: boolean;
  preferences: UserPreferences;
  roleRefs: readonly string[];
}

export interface LoginCredentials {
  username: string;
  password: string;
}

export interface LoginResult {
  ok: boolean;
  user?: CurrentUserPayload | null;
}

export interface UserPreferencesState {
  preferences: UserPreferences;
  setPreferences: (preferences: UserPreferences) => Promise<void>;
}

export interface AngeeAuthProviderOptions extends AngeeHasuraClientOptions {
  loginPath?: string;
  onAuthChange?: () => void;
}

export interface UseRuntimeAuthStateResult {
  auth: AuthState;
  fetching: boolean;
  error: Error | null;
}

export interface UseUpdatePreferencesOptions {
  dataProviderName?: string;
}

export interface UseUpdatePreferencesResult {
  updatePreferences: (preferences: UserPreferences) => Promise<CurrentUserPayload | null>;
  fetching: boolean;
  error: Error | null;
}

export interface UpdatePreferencesRequest {
  url: "";
  method: "post";
  values: { preferences: UserPreferences };
  dataProviderName?: string;
  meta: MetaQuery;
}

type GraphQLRequest = <TData, TVariables extends object = Record<string, never>>(
  document: string,
  variables?: TVariables,
) => Promise<TData>;

interface CurrentUserQueryResult {
  current_user: unknown;
}

interface LoginMutationResult {
  login?: {
    ok?: unknown;
    user?: unknown;
  } | null;
}

interface LogoutMutationResult {
  logout?: unknown;
}

interface UpdatePreferencesMutationResult {
  update_preferences?: unknown;
}

interface AngeeAuthActionResponse extends AuthActionResponse {
  ok?: boolean;
  user?: CurrentUserPayload | null;
}

const PUBLIC_USER_SELECTION =
  "id username firstName: first_name lastName: last_name email isStaff: is_staff isActive: is_active preferences";
const CURRENT_USER_SELECTION = `${PUBLIC_USER_SELECTION} roleRefs: role_refs`;

export const CURRENT_USER_DOCUMENT =
  `query angee_current_user { current_user { ${CURRENT_USER_SELECTION} } }`;

export const LOGIN_DOCUMENT =
  `mutation angeeLogin($username: String!, $password: String!) { ` +
  `login(username: $username, password: $password) { ok user { ${PUBLIC_USER_SELECTION} } } }`;

export const LOGOUT_DOCUMENT = "mutation angeeLogout { logout }";

export const UPDATE_PREFERENCES_DOCUMENT =
  `mutation angee_update_preferences($preferences: JSON!) { ` +
  `update_preferences(preferences: $preferences) { ${CURRENT_USER_SELECTION} } }`;
export const UPDATE_PREFERENCES_MUTATION = parse(UPDATE_PREFERENCES_DOCUMENT);

export const ANONYMOUS_AUTH: AuthState = {
  user: null,
  status: "anonymous",
  hasRole: () => false,
};

const EMPTY_PREFERENCES: UserPreferences = {};
const DEFAULT_PREFERENCES_STATE: UserPreferencesState = {
  preferences: EMPTY_PREFERENCES,
  setPreferences: async () => undefined,
};

const AuthContext = createContext<AuthState | null>(null);
const UserPreferencesContext = createContext<UserPreferencesState | null>(null);

export function createAngeeAuthProvider(
  options: AngeeAuthProviderOptions,
): RefineAuthProvider {
  const client = createAngeeGraphQLClient(options);
  const request = client.request.bind(client) as GraphQLRequest;
  return createAngeeAuthProviderFromRequest(request, options);
}

export function createAngeeAuthProviderFromRequest(
  request: GraphQLRequest,
  options: Pick<AngeeAuthProviderOptions, "loginPath" | "onAuthChange"> = {},
): RefineAuthProvider {
  const loginPath = options.loginPath ?? "/login";
  const currentUser = async (): Promise<CurrentUserPayload | null> => {
    const data = await request<CurrentUserQueryResult>(CURRENT_USER_DOCUMENT);
    return parseCurrentUser(recordValue(data)?.current_user);
  };
  return {
    async check() {
      try {
        const user = await currentUser();
        return user
          ? { authenticated: true }
          : { authenticated: false, redirectTo: loginPath };
      } catch (caught) {
        return { authenticated: false, error: errorFromUnknown(caught) };
      }
    },
    async getIdentity() {
      const payload = await currentUser();
      return currentUserToAuthState(payload).user;
    },
    async getPermissions() {
      const payload = await currentUser();
      return payload?.roleRefs ?? [];
    },
    async login(params) {
      try {
        const credentials = loginCredentials(params);
        if (!credentials) return { success: false, ok: false };
        const data = await request<LoginMutationResult, LoginCredentials>(
          LOGIN_DOCUMENT,
          credentials,
        );
        const login = recordValue(data.login);
        const ok = login?.ok === true;
        if (ok) options.onAuthChange?.();
        return {
          success: ok,
          ok,
          user: parseCurrentUser(login?.user),
        } satisfies AngeeAuthActionResponse;
      } catch (caught) {
        return { success: false, error: errorFromUnknown(caught) };
      }
    },
    async logout() {
      try {
        const data = await request<LogoutMutationResult>(LOGOUT_DOCUMENT);
        const success = data.logout === true;
        if (success) options.onAuthChange?.();
        return { success };
      } catch (caught) {
        return { success: false, error: errorFromUnknown(caught) };
      }
    },
    async onError(error) {
      const resolved = errorFromUnknown(error);
      return isUnauthorizedError(error)
        ? { logout: true, redirectTo: loginPath, error: resolved }
        : { error: resolved };
    },
  };
}

export function useRuntimeAuthState(): UseRuntimeAuthStateResult {
  const identity = useGetIdentity<AuthUser | null>({
    queryOptions: { retry: false },
  });
  const auth = useMemo(
    () => authStateFromUser(identity.data ?? null),
    [identity.data],
  );
  return {
    auth,
    fetching: identity.isFetching,
    error: errorFromUnknownOrNull(identity.error),
  };
}

export function useLoginWithPassword(): {
  login: (credentials: LoginCredentials) => Promise<LoginResult>;
  fetching: boolean;
  error: Error | null;
} {
  const mutation = useLogin<LoginCredentials>();
  const login = useCallback(
    async (credentials: LoginCredentials): Promise<LoginResult> => {
      const response = await mutation.mutateAsync(credentials) as AngeeAuthActionResponse;
      if (response.error) throw response.error;
      return {
        ok: response.ok ?? response.success,
        user: response.user ?? null,
      };
    },
    [mutation.mutateAsync],
  );
  return {
    login,
    fetching: mutation.isPending,
    error: errorFromUnknownOrNull(mutation.error),
  };
}

export function useLogout(): {
  logout: () => Promise<boolean>;
  fetching: boolean;
  error: Error | null;
} {
  const mutation = useRefineLogout();
  const logout = useCallback(async (): Promise<boolean> => {
    const response = await mutation.mutateAsync({ redirectPath: false });
    if (response.error) throw response.error;
    return response.success;
  }, [mutation.mutateAsync]);
  return {
    logout,
    fetching: mutation.isPending,
    error: errorFromUnknownOrNull(mutation.error),
  };
}

export function updatePreferencesRequest(
  preferences: UserPreferences,
  dataProviderName?: string,
): UpdatePreferencesRequest {
  return {
    url: "",
    method: "post",
    values: { preferences },
    dataProviderName,
    meta: mutationMeta(UPDATE_PREFERENCES_MUTATION, { preferences }),
  };
}

export function useUpdatePreferences(
  options: UseUpdatePreferencesOptions = {},
): UseUpdatePreferencesResult {
  const run = useCustomMutation<
    BaseRecord,
    HttpError,
    { preferences: UserPreferences }
  >();
  const invalidateAuthStore = useInvalidateAuthStore();
  const updatePreferences = useCallback(
    async (preferences: UserPreferences): Promise<CurrentUserPayload | null> => {
      const response = await run.mutateAsync(
        updatePreferencesRequest(preferences, options.dataProviderName),
      );
      await invalidateAuthStore();
      return parseCurrentUser(
        recordValue(response.data as UpdatePreferencesMutationResult)
          ?.update_preferences,
      );
    },
    [invalidateAuthStore, options.dataProviderName, run.mutateAsync],
  );
  return {
    updatePreferences,
    fetching: run.mutation.isPending,
    error: errorFromUnknownOrNull(run.mutation.error),
  };
}

export function AuthProvider({
  auth,
  children,
}: {
  auth: Partial<Pick<AuthState, "user" | "status">>;
  children: ReactNode;
}): ReactNode {
  const value = useMemo<AuthState>(() => {
    const user = auth.user ?? null;
    return {
      user,
      status: auth.status ?? (user ? "authenticated" : "anonymous"),
      hasRole: (role) => Boolean(user?.roles?.includes(role)),
    };
  }, [auth]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  return useContext(AuthContext) ?? ANONYMOUS_AUTH;
}

export function UserPreferencesProvider({
  children,
  dataProviderName,
}: {
  children: ReactNode;
  dataProviderName?: string;
}): ReactNode {
  const { user } = useAuth();
  const { updatePreferences } = useUpdatePreferences({ dataProviderName });
  const preferences = user?.preferences ?? EMPTY_PREFERENCES;
  const setPreferences = useCallback(
    async (next: UserPreferences): Promise<void> => {
      if (!user) return;
      await updatePreferences(next);
    },
    [updatePreferences, user],
  );
  const value = useMemo<UserPreferencesState>(
    () => ({ preferences, setPreferences }),
    [preferences, setPreferences],
  );
  return (
    <UserPreferencesContext.Provider value={value}>
      {children}
    </UserPreferencesContext.Provider>
  );
}

export function useUserPreferences(): UserPreferencesState {
  return useContext(UserPreferencesContext) ?? DEFAULT_PREFERENCES_STATE;
}

export function parseCurrentUser(value: unknown): CurrentUserPayload | null {
  const record = recordValue(value);
  if (!record) return null;
  if (typeof record.id !== "string" || typeof record.username !== "string") {
    return null;
  }
  return {
    id: record.id,
    username: record.username,
    firstName: stringValue(record.firstName),
    lastName: stringValue(record.lastName),
    email: stringValue(record.email),
    isStaff: record.isStaff === true,
    isActive: record.isActive === true,
    preferences: preferencesValue(record.preferences),
    roleRefs: stringList(record.roleRefs),
  };
}

export function currentUserToAuthState(
  payload: CurrentUserPayload | null | undefined,
): AuthState {
  if (!payload) return ANONYMOUS_AUTH;
  const fullName = `${payload.firstName} ${payload.lastName}`.trim();
  const user: AuthUser = {
    id: payload.id,
    name: fullName || payload.username,
    username: payload.username,
    firstName: payload.firstName,
    lastName: payload.lastName,
    email: payload.email || undefined,
    isStaff: payload.isStaff,
    isActive: payload.isActive,
    preferences: payload.preferences,
    roles: payload.roleRefs,
  };
  return authStateFromUser(user);
}

function authStateFromUser(user: AuthUser | null): AuthState {
  if (!user) return ANONYMOUS_AUTH;
  return {
    user,
    status: "authenticated",
    hasRole: (role) => Boolean(user.roles?.includes(role)),
  };
}

function loginCredentials(value: unknown): LoginCredentials | null {
  const record = recordValue(value);
  if (!record) return null;
  return typeof record.username === "string" && typeof record.password === "string"
    ? { username: record.username, password: record.password }
    : null;
}

function preferencesValue(value: unknown): UserPreferences {
  const record = recordValue(value);
  return record ? { ...record } : {};
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function stringList(value: unknown): readonly string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function errorFromUnknownOrNull(value: unknown): Error | null {
  return value == null ? null : errorFromUnknown(value);
}

function errorFromUnknown(value: unknown): Error {
  if (value instanceof Error) return value;
  const record = recordValue(value);
  if (typeof record?.message === "string") return new Error(record.message);
  return new Error("GraphQL auth request failed");
}

function isUnauthorizedError(value: unknown): boolean {
  const record = recordValue(value);
  const response = recordValue(record?.response);
  return response?.status === 401 || record?.statusCode === 401 || record?.status === 401;
}

function mutationMeta(
  gqlMutation: DocumentNode,
  gqlVariables: Record<string, unknown>,
): MetaQuery {
  return { gqlMutation, gqlVariables } as unknown as MetaQuery;
}
