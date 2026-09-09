import { describe, expect, test, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";

import {
  createAngeeAuthProviderFromRequest,
  currentUserToAuthState,
  identityQueryOptions,
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

  test("rejects a transient identity failure with bounded transport copy", async () => {
    const sentinel = "identity-request-secret";
    const provider = createAngeeAuthProviderFromRequest(async () => {
      const error = new Error(`GraphQL Error (Code: 502): request variables ${sentinel}`);
      Object.assign(error, { response: { status: 502 }, request: { variables: sentinel } });
      throw error;
    });

    await expect(provider.check()).rejects.toThrow("Request failed.");
  });

  test("does not invent an authenticated session on an initial transport failure", async () => {
    const provider = createAngeeAuthProviderFromRequest(async () => {
      throw Object.assign(new Error("gateway"), { response: { status: 502 }, request: {} });
    });
    await expect(provider.check()).rejects.toThrow("Request failed.");
  });

  test("redirects when the identity endpoint explicitly returns 401", async () => {
    const provider = createAngeeAuthProviderFromRequest(async () => {
      throw Object.assign(new Error("unauthorized"), { response: { status: 401 } });
    });

    await expect(provider.check()).resolves.toEqual(expect.objectContaining({
      authenticated: false,
      redirectTo: "/login",
    }));
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

  test("does not log out a valid Django session for another provider's 401", async () => {
    const request = vi.fn(async (document: unknown) => {
      expect(document).toBe(AngeeCurrentUserDocument);
      return { current_user: currentUser };
    });
    const provider = createAngeeAuthProviderFromRequest(request as never);
    const error = Object.assign(new Error("operator token expired"), {
      response: { status: 401 },
    });

    await expect(provider.onError(error)).resolves.toEqual({
      error: expect.objectContaining({ message: "This request requires authentication." }),
    });
    expect(request).toHaveBeenCalledTimes(1);
  });

  test("freshly verifies a cached positive identity and deduplicates concurrent 401 probes", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let resolveProbe!: (value: { current_user: null }) => void;
    const request = vi.fn(() => new Promise<{ current_user: null }>((resolve) => {
      resolveProbe = resolve;
    }));
    const provider = createAngeeAuthProviderFromRequest(request as never, { queryClient });
    queryClient.setQueryData(identityQueryOptions(provider).queryKey, {
      id: "user_1", name: "Ada Lovelace",
    });
    const error = Object.assign(new Error("expired"), { response: { status: 401 } });
    const first = provider.onError(error);
    const second = provider.onError(error);
    resolveProbe({ current_user: null });
    await expect(Promise.all([first, second])).resolves.toEqual([
      expect.objectContaining({ logout: true }),
      expect.objectContaining({ logout: true }),
    ]);
    expect(request).toHaveBeenCalledTimes(1);
  });

  test.each([
    ["anonymous", async () => ({ current_user: null })],
    ["unauthorized", async () => { throw Object.assign(new Error("unauthorized"), { response: { status: 401 } }); }],
  ])("logs out after a %s authoritative identity response", async (_case, request) => {
    const provider = createAngeeAuthProviderFromRequest(request as never);
    const error = Object.assign(new Error("request unauthorized"), {
      response: { status: 401 },
    });

    await expect(provider.onError(error)).resolves.toEqual({
      logout: true,
      redirectTo: "/login",
      error: expect.objectContaining({ message: "This request requires authentication." }),
    });
  });

  test("preserves the session when the authoritative identity probe fails transiently", async () => {
    const provider = createAngeeAuthProviderFromRequest(async () => {
      throw Object.assign(new Error("gateway unavailable"), { response: { status: 502 } });
    });
    const error = Object.assign(new Error("request unauthorized"), {
      response: { status: 401 },
    });

    await expect(provider.onError(error)).resolves.toEqual({
      error: expect.objectContaining({ message: "This request requires authentication." }),
    });
  });

  test.each(["status", "statusCode"])(
    "bounds sensitive top-level %s unauthorized errors",
    async (field) => {
      const sentinel = "provider-secret-must-not-render";
      const provider = createAngeeAuthProviderFromRequest(async () => ({ current_user: currentUser }) as never);
      const error = Object.assign(new Error(sentinel), { [field]: 401 });

      const result = await provider.onError(error);
      expect(result).not.toHaveProperty("logout");
      expect(result.error?.message).toBe("This request requires authentication.");
      expect(result.error?.message).not.toContain(sentinel);
    },
  );

  test.each([
    Object.assign(new Error("forbidden"), { response: { status: 403 } }),
    Object.assign(new Error("permission denied"), {
      response: { errors: [{ message: "Permission denied.", extensions: { code: "FORBIDDEN" } }] },
    }),
  ])("reports permission failures without probing identity or logging out", async (error) => {
    const request = vi.fn();
    const provider = createAngeeAuthProviderFromRequest(request as never);

    const result = await provider.onError(error);
    expect(result).not.toHaveProperty("logout");
    expect(result).not.toHaveProperty("redirectTo");
    expect(result.error).toBeInstanceOf(Error);
    expect(request).not.toHaveBeenCalled();
  });

  test("auth state uses role refs for role checks", () => {
    const auth = currentUserToAuthState(currentUser);

    expect(auth.status).toBe("authenticated");
    expect(auth.hasRole("angee/role:admin")).toBe(true);
    expect(auth.hasRole("angee/role:viewer")).toBe(false);
  });

});
