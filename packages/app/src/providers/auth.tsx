import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import {
  keys,
  useGetIdentity,
  useInvalidateAuthStore,
  useLogin,
  useLogout as useRefineLogout,
  type AuthActionResponse,
  type AuthProvider as RefineAuthProvider,
} from "@refinedev/core";

import {
  createAngeeGraphQLClient,
  useAuthoredMutation,
  recordValue,
  type AngeeHasuraClientOptions,
  type TypedDocumentNode,
} from "@angee/refine";
import { errorFromUnknown as sharedErrorFromUnknown } from "@angee/ui/data/errors";
import { DEFAULT_LOGIN_PATH } from "@angee/ui/runtime";
import {
  AngeeCurrentUserDocument,
  AngeeLoginDocument,
  AngeeLogoutDocument,
  AngeeUpdatePreferencesDocument,
  type AngeeCurrentUserData,
  type AngeeLoginUserData,
} from "./documents.public";

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

export type CurrentUserPayload = Omit<
  NonNullable<AngeeCurrentUserData>,
  "preferences"
> & {
  preferences: UserPreferences;
};

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

type GraphQLRequest = <TData, TVariables extends object = Record<string, never>>(
  document: TypedDocumentNode<TData, TVariables>,
  variables?: TVariables,
) => Promise<TData>;

interface AngeeAuthActionResponse extends AuthActionResponse {
  ok?: boolean;
  user?: CurrentUserPayload | null;
}

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
  const loginPath = options.loginPath ?? DEFAULT_LOGIN_PATH;
  const currentUser = async (): Promise<CurrentUserPayload | null> => {
    const data = await request(AngeeCurrentUserDocument);
    return currentUserPayload(data.current_user);
  };
  return {
    async check() {
      try {
        const user = await currentUser();
        return user
          ? { authenticated: true }
          : { authenticated: false, redirectTo: loginPath };
      } catch (caught) {
        return { authenticated: false, error: authErrorFromUnknown(caught) };
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
        const data = await request(
          AngeeLoginDocument,
          credentials,
        );
        const ok = data.login.ok;
        if (ok) options.onAuthChange?.();
        return {
          success: ok,
          ok,
          user: loginUserPayload(data.login.user),
        } satisfies AngeeAuthActionResponse;
      } catch (caught) {
        return { success: false, error: authErrorFromUnknown(caught) };
      }
    },
    async logout() {
      try {
        const data = await request(AngeeLogoutDocument);
        const success = data.logout;
        if (success) options.onAuthChange?.();
        return { success };
      } catch (caught) {
        return { success: false, error: authErrorFromUnknown(caught) };
      }
    },
    async onError(error) {
      const resolved = authErrorFromUnknown(error);
      return isUnauthorizedError(error)
        ? { logout: true, redirectTo: loginPath, error: resolved }
        : { error: resolved };
    },
  };
}

/**
 * The identity-query contract, owned once. Refine's `useGetIdentity` reads the
 * react-query entry keyed `keys().auth().action("identity")`; the route gate
 * (`@angee/app` `beforeLoad`) reaches that SAME entry through
 * `queryClient.ensureQueryData(identityQueryOptions(authProvider))`, so the gate
 * and `useRuntimeAuthState` below share ONE `current_user` fetch instead of
 * each issuing their own. `staleTime: Infinity` keeps warm navigations from
 * re-issuing it — refine's `useInvalidateAuthStore` (login/logout) refreshes
 * the entry, and a mid-session server expiry still surfaces at the data layer as
 * a 401 → `onError` → logout (client gates are UX only; the server is the
 * authorization boundary).
 */
export const IDENTITY_STALE_TIME = Number.POSITIVE_INFINITY;
const IDENTITY_QUERY_SETTINGS = {
  staleTime: IDENTITY_STALE_TIME,
  retry: false,
} as const;

export function identityQueryOptions(authProvider: RefineAuthProvider) {
  return {
    queryKey: keys().auth().action("identity").get(),
    queryFn: async (): Promise<AuthUser | null> =>
      ((await authProvider.getIdentity?.()) ?? null) as AuthUser | null,
    ...IDENTITY_QUERY_SETTINGS,
  };
}

export function useRuntimeAuthState(): UseRuntimeAuthStateResult {
  const identity = useGetIdentity<AuthUser | null>({
    queryOptions: IDENTITY_QUERY_SETTINGS,
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

export function useLogoutAction(): {
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

export function useUpdatePreferences(
  options: UseUpdatePreferencesOptions = {},
): UseUpdatePreferencesResult {
  const [run, mutation] = useAuthoredMutation(
    AngeeUpdatePreferencesDocument,
    { dataProviderName: options.dataProviderName },
  );
  const invalidateAuthStore = useInvalidateAuthStore();
  const updatePreferences = useCallback(
    async (preferences: UserPreferences): Promise<CurrentUserPayload | null> => {
      const data = await run({ preferences });
      await invalidateAuthStore();
      return currentUserPayload(data?.update_preferences);
    },
    [invalidateAuthStore, run],
  );
  return {
    updatePreferences,
    fetching: mutation.fetching,
    error: mutation.error,
  };
}

export function AuthStateProvider({
  auth,
  children,
}: {
  auth: AuthState;
  children: ReactNode;
}): ReactNode {
  return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>;
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

function currentUserPayload(
  value: AngeeCurrentUserData | null | undefined,
): CurrentUserPayload | null {
  if (!value) return null;
  return {
    ...value,
    preferences: preferencesValue(value.preferences),
  };
}

function loginUserPayload(
  value: AngeeLoginUserData | null | undefined,
): CurrentUserPayload | null {
  if (!value) return null;
  return {
    ...value,
    preferences: preferencesValue(value.preferences),
    roleRefs: [],
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

function errorFromUnknownOrNull(value: unknown): Error | null {
  return value == null ? null : sharedErrorFromUnknown(value);
}

function authErrorFromUnknown(value: unknown): Error {
  return sharedErrorFromUnknown(value) ?? new Error("GraphQL auth request failed");
}

function isUnauthorizedError(value: unknown): boolean {
  const record = recordValue(value);
  const response = recordValue(record?.response);
  return response?.status === 401 || record?.statusCode === 401 || record?.status === 401;
}
