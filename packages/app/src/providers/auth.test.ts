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

  test("auth state uses role refs for role checks", () => {
    const auth = currentUserToAuthState(currentUser);

    expect(auth.status).toBe("authenticated");
    expect(auth.hasRole("angee/role:admin")).toBe(true);
    expect(auth.hasRole("angee/role:viewer")).toBe(false);
  });

});
