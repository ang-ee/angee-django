import type { APIRequestContext } from "@playwright/test";

import { GraphQLClient, PUBLIC_GRAPHQL_PATH } from "./graphql";

export interface Credentials {
  username: string;
  password: string;
}

const LOGIN_MUTATION = `mutation Login($username: String!, $password: String!) {
  login(username: $username, password: $password) {
    ok
    user { id username }
  }
}`;

/**
 * Log a user in over the GraphQL `login` mutation, leaving the session cookie in
 * the request context. Throws if the backend reports the login failed, so a
 * broken seed or credential surfaces in the setup project rather than as a
 * confusing "logged out" failure deep in a spec.
 */
export async function loginViaApi(
  request: APIRequestContext,
  credentials: Credentials,
): Promise<void> {
  const result = await new GraphQLClient(request, PUBLIC_GRAPHQL_PATH).query<{
    login: { ok: boolean };
  }>(LOGIN_MUTATION, { ...credentials });
  if (!result.data?.login?.ok) {
    const reason = JSON.stringify(result.errors ?? result.data ?? null);
    throw new Error(`login failed for "${credentials.username}": ${reason}`);
  }
}

/**
 * Where a role's persisted auth state lives, relative to the test project. The
 * setup project writes it; specs load it with
 * `test.use({ storageState: roleStatePath("alice") })`.
 */
export function roleStatePath(role: string): string {
  return `.auth/${role}.json`;
}
