import { parse } from "graphql";

import type { TypedDocumentNode } from "@angee/refine";

interface CurrentUserFields {
  id: string;
  username: string;
  email: string;
  preferences: unknown;
  firstName: string;
  lastName: string;
  isStaff: boolean;
  isActive: boolean;
  roleRefs: string[];
}

interface LoginUserFields {
  id: string;
  username: string;
  email: string;
  preferences: unknown;
  firstName: string;
  lastName: string;
  isStaff: boolean;
  isActive: boolean;
}

interface AngeeCurrentUserResult {
  current_user: CurrentUserFields | null;
}

interface AngeeLoginResult {
  login: {
    ok: boolean;
    user: LoginUserFields | null;
  };
}

interface AngeeLogoutResult {
  logout: boolean;
}

interface AngeeUpdatePreferencesResult {
  update_preferences: CurrentUserFields;
}

function authDocument<TResult, TVariables extends object>(
  source: string,
): TypedDocumentNode<TResult, TVariables> {
  return parse(source) as TypedDocumentNode<TResult, TVariables>;
}

export const AngeeCurrentUserDocument = authDocument<
  AngeeCurrentUserResult,
  Record<string, never>
>(`
  query AngeeCurrentUser {
    current_user {
      id
      username
      firstName: first_name
      lastName: last_name
      email
      isStaff: is_staff
      isActive: is_active
      preferences
      roleRefs: role_refs
    }
  }
`);

export const AngeeLoginDocument = authDocument<
  AngeeLoginResult,
  { username: string; password: string }
>(`
  mutation AngeeLogin($username: String!, $password: String!) {
    login(username: $username, password: $password) {
      ok
      user {
        id
        username
        firstName: first_name
        lastName: last_name
        email
        isStaff: is_staff
        isActive: is_active
        preferences
      }
    }
  }
`);

export const AngeeLogoutDocument = authDocument<
  AngeeLogoutResult,
  Record<string, never>
>(`
  mutation AngeeLogout {
    logout
  }
`);

export const AngeeUpdatePreferencesDocument = authDocument<
  AngeeUpdatePreferencesResult,
  { preferences: unknown }
>(`
  mutation AngeeUpdatePreferences($preferences: JSON!) {
    update_preferences(preferences: $preferences) {
      id
      username
      firstName: first_name
      lastName: last_name
      email
      isStaff: is_staff
      isActive: is_active
      preferences
      roleRefs: role_refs
    }
  }
`);

export type AngeeCurrentUserData = AngeeCurrentUserResult["current_user"];

export type AngeeLoginUserData = AngeeLoginResult["login"]["user"];
