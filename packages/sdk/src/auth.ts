import { useMemo } from "react";

import { makeContext } from "./make-context";

/** The signed-in user, as the auth query resolves it. */
export interface AuthUser {
  id: string;
  name: string;
  email?: string;
  roles?: readonly string[];
  username?: string;
  isStaff?: boolean;
  isSuperuser?: boolean;
}

/** Client-side auth state. Gating from it is UX only; the server authorizes. */
export interface AuthState {
  user: AuthUser | null;
  status: "anonymous" | "authenticated";
  hasRole: (role: string) => boolean;
}

/** The shared anonymous state: no user, every role check false. */
export const ANONYMOUS_AUTH: AuthState = {
  user: null,
  status: "anonymous",
  hasRole: () => false,
};

/** The `currentUser` payload shape the auth query resolves. */
export interface CurrentUserPayload {
  id: string;
  username: string;
  email?: string | null;
  isStaff: boolean;
  isSuperuser: boolean;
  roles: readonly string[];
}

/** Map a resolved (or null) `currentUser` to an `AuthState`. */
export function currentUserToAuthState(
  payload: CurrentUserPayload | null | undefined,
): AuthState {
  if (!payload) return ANONYMOUS_AUTH;
  const roles = payload.roles;
  const user: AuthUser = {
    id: payload.id,
    name: payload.username,
    email: payload.email ?? undefined,
    roles,
    username: payload.username,
    isStaff: payload.isStaff,
    isSuperuser: payload.isSuperuser,
  };
  return {
    user,
    status: "authenticated",
    hasRole: (role: string) => roles.includes(role),
  };
}

const AuthContext = makeContext<AuthState>("AuthProvider");

/**
 * Provide auth state. Pass the `user`/`status` an auth query resolved; `hasRole`
 * is derived from the user's roles so call sites never re-implement the check.
 */
export function AuthProvider(props: {
  auth: Partial<Pick<AuthState, "user" | "status">>;
  children: React.ReactNode;
}): React.ReactNode {
  const { auth } = props;
  const value = useMemo<AuthState>(() => {
    const user = auth.user ?? null;
    const roles = user?.roles ?? [];
    return {
      user,
      status: auth.status ?? (user ? "authenticated" : "anonymous"),
      hasRole: (role: string) => roles.includes(role),
    };
  }, [auth]);
  return AuthContext.Provider({ value, children: props.children });
}

/** Current auth state, anonymous when unprovided. */
export function useAuth(): AuthState {
  return AuthContext.useMaybe() ?? ANONYMOUS_AUTH;
}
