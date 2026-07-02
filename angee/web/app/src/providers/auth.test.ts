import { buildSchema, parse, print, validate } from "graphql";
import { describe, expect, test, vi } from "vitest";

import {
  CURRENT_USER_DOCUMENT,
  LOGIN_DOCUMENT,
  LOGOUT_DOCUMENT,
  UPDATE_PREFERENCES_DOCUMENT,
  UPDATE_PREFERENCES_MUTATION,
  createAngeeAuthProviderFromRequest,
  currentUserToAuthState,
  parseCurrentUser,
  updatePreferencesRequest,
} from "./auth";

const contract = buildSchema(`
  scalar JSON

  interface Node {
    id: ID!
  }

  type CurrentUserType implements Node {
    id: ID!
    username: String!
    first_name: String!
    last_name: String!
    email: String!
    is_staff: Boolean!
    is_active: Boolean!
    preferences: JSON!
    role_refs: [String!]!
  }

  type UserType implements Node {
    id: ID!
    username: String!
    first_name: String!
    last_name: String!
    email: String!
    is_staff: Boolean!
    is_active: Boolean!
    preferences: JSON!
  }

  type LoginPayload {
    ok: Boolean!
    user: UserType
  }

  type Query {
    current_user: CurrentUserType
  }

  type Mutation {
    login(username: String!, password: String!): LoginPayload!
    logout: Boolean!
    update_preferences(preferences: JSON!): CurrentUserType!
  }
`);

const currentUser = {
  id: "user_1",
  username: "ada",
  firstName: "Ada",
  lastName: "Lovelace",
  email: "ada@example.com",
  isStaff: true,
  isActive: true,
  preferences: { chrome: "compact" },
  roleRefs: ["angee/role:admin"],
};

describe("Angee app auth provider", () => {
  for (const [name, document] of [
    ["current_user", CURRENT_USER_DOCUMENT],
    ["login", LOGIN_DOCUMENT],
    ["logout", LOGOUT_DOCUMENT],
    ["update_preferences", UPDATE_PREFERENCES_DOCUMENT],
  ] as const) {
    test(`${name} document validates against the public schema`, () => {
      expect(validate(contract, parse(document)).map((error) => error.message)).toEqual([]);
    });
  }

  test("maps currentUser into Refine identity and permissions", async () => {
    const provider = createAngeeAuthProviderFromRequest(async (document) => {
      expect(document).toBe(CURRENT_USER_DOCUMENT);
      return { current_user: currentUser } as never;
    });

    await expect(provider.check()).resolves.toEqual({ authenticated: true });
    await expect(provider.getIdentity?.()).resolves.toEqual(
      expect.objectContaining({
        id: "user_1",
        name: "Ada Lovelace",
        roles: ["angee/role:admin"],
      }),
    );
    await expect(provider.getPermissions?.()).resolves.toEqual([
      "angee/role:admin",
    ]);
  });

  test("returns an unauthenticated check response when currentUser is empty", async () => {
    const provider = createAngeeAuthProviderFromRequest(async () => ({
      current_user: null,
    }) as never);

    await expect(provider.check()).resolves.toEqual({
      authenticated: false,
      redirectTo: "/login",
    });
  });

  test("logs in and logs out through the Refine auth contract", async () => {
    const onAuthChange = vi.fn();
    const request = vi.fn(async (document: string, variables?: object) => {
      if (document === LOGIN_DOCUMENT) {
        expect(variables).toEqual({ username: "ada", password: "secret" });
        return { login: { ok: true, user: currentUser } };
      }
      if (document === LOGOUT_DOCUMENT) return { logout: true };
      throw new Error(`Unexpected document: ${document}`);
    });
    const provider = createAngeeAuthProviderFromRequest(request as never, {
      onAuthChange,
    });

    await expect(
      provider.login({ username: "ada", password: "secret" }),
    ).resolves.toEqual(
      expect.objectContaining({
        success: true,
        ok: true,
        user: expect.objectContaining({ username: "ada" }),
      }),
    );
    await expect(provider.logout({})).resolves.toEqual({ success: true });
    expect(onAuthChange).toHaveBeenCalledTimes(2);
  });

  test("auth state uses role refs for role checks", () => {
    const auth = currentUserToAuthState(parseCurrentUser(currentUser));

    expect(auth.status).toBe("authenticated");
    expect(auth.hasRole("angee/role:admin")).toBe(true);
    expect(auth.hasRole("angee/role:viewer")).toBe(false);
  });

  test("preferences update uses the authored Hasura mutation path", () => {
    const preferences = { density: "compact" };

    expect(updatePreferencesRequest(preferences, "public")).toEqual({
      url: "",
      method: "post",
      values: { preferences },
      dataProviderName: "public",
      meta: {
        gqlMutation: UPDATE_PREFERENCES_MUTATION,
        gqlVariables: { preferences },
      },
    });
    const mutation = updatePreferencesRequest(preferences).meta.gqlMutation;
    expect(mutation).toBeDefined();
    expect(printDocument(mutation)).toBe(compact(print(UPDATE_PREFERENCES_MUTATION)));
  });
});

function compact(document: string): string {
  return document.replace(/\s+/g, " ").trim();
}

function printDocument(document: unknown): string {
  expect(document).toBeDefined();
  return compact(print(document as Parameters<typeof print>[0]));
}
