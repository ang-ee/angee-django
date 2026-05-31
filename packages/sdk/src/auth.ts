import { useMemo } from "react";

import { makeContext } from "./make-context";

/** The signed-in user, as the auth query resolves it. */
export interface AuthUser {
  id: string;
  name: string;
  username?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
  isStaff?: boolean;
  isActive?: boolean;
  roles?: readonly string[];
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

/** The `currentUser` payload shape the auth query resolves (the User node). */
export interface CurrentUserPayload {
  id: string;
  username: string;
  firstName: string;
  lastName: string;
  email: string;
  isStaff: boolean;
  isActive: boolean;
}

const asString = (value: unknown): string => (typeof value === "string" ? value : "");

/**
 * Narrow an untrusted `currentUser` value to the payload, or null. A signed-in
 * user always has a string `id` and `username`; the rest is coerced so a partial
 * response can never surface as `"undefined undefined"` downstream.
 */
export function parseCurrentUser(value: unknown): CurrentUserPayload | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  if (typeof record.id !== "string" || typeof record.username !== "string") {
    return null;
  }
  return {
    id: record.id,
    username: record.username,
    firstName: asString(record.firstName),
    lastName: asString(record.lastName),
    email: asString(record.email),
    isStaff: record.isStaff === true,
    isActive: record.isActive === true,
  };
}

/**
 * Map a resolved (or null) `currentUser` to an `AuthState`. Roles are not on the
 * User node — REBAC owns authorization, and role-gating in the UI is deferred —
 * so `hasRole` is always false here; the server is the authorization boundary.
 */
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
    roles: [],
  };
  return { user, status: "authenticated", hasRole: () => false };
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
