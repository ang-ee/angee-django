import { describe, expect, test, vi } from "vitest";

import {
  createAngeeAuthProviderFromRequest,
  currentUserToAuthState,
} from "./auth";
import {
  AngeeCurrentUserDocument,
  AngeeLoginDocument,
  AngeeLogoutDocument,
} from "./documents.public";

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
  test("maps currentUser into Refine identity and permissions", async () => {
    const provider = createAngeeAuthProviderFromRequest(async (document) => {
      expect(document).toBe(AngeeCurrentUserDocument);
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
    const request = vi.fn(async (document: unknown, variables?: object) => {
      if (document === AngeeLoginDocument) {
        expect(variables).toEqual({ username: "ada", password: "secret" });
        return { login: { ok: true, user: currentUser } };
      }
      if (document === AngeeLogoutDocument) return { logout: true };
      throw new Error("Unexpected document");
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

  test("never exposes login request variables from a transport error", async () => {
    const sentinel = "password-must-never-render";
    const provider = createAngeeAuthProviderFromRequest(async () => {
      const error = new Error(`GraphQL Error: request variables { password: ${sentinel} }`);
      Object.assign(error, { response: { status: 429, error: "Account locked" } });
      throw error;
    });

    const result = await provider.login({ username: "admin", password: sentinel });
    expect(result.success).toBe(false);
    expect(result.error?.message).toBe(
      "Too many sign-in attempts. Try again later or contact an administrator.",
    );
    expect(result.error?.message).not.toContain(sentinel);
    expect(result.error?.message).not.toContain("variables");
  });

  test.each([
    [{ response: { status: 401 } }, "Invalid username or password."],
    [{ response: { errors: [{ message: "denied", extensions: { code: "UNAUTHENTICATED" } }] } }, "Invalid username or password."],
    [new Error("query and secret variables"), "Sign-in request failed. Please try again."],
  ])("maps auth failures to bounded user-facing copy", async (caught, expected) => {
    const provider = createAngeeAuthProviderFromRequest(async () => { throw caught; });
    const result = await provider.login({ username: "ada", password: "sentinel" });
    expect(result.error?.message).toBe(expected);
    expect(result.error?.message).not.toContain("sentinel");
  });

  test("preserves the normal invalid-credential result without an error message", async () => {
    const provider = createAngeeAuthProviderFromRequest(async () => ({
      login: { ok: false, user: null },
    }) as never);
    await expect(provider.login({ username: "ada", password: "wrong" })).resolves.toEqual({
      success: false,
      ok: false,
      user: null,
    });
  });

  test("auth state uses role refs for role checks", () => {
    const auth = currentUserToAuthState(currentUser);

    expect(auth.status).toBe("authenticated");
    expect(auth.hasRole("angee/role:admin")).toBe(true);
    expect(auth.hasRole("angee/role:viewer")).toBe(false);
  });

});
