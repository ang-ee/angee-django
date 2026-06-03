import { test, expect } from "@angee/e2e";

import { LoginPage } from "../pages/login-page";

// These run anonymously (no storageState) — they exercise the real login UI for
// every demo login, not the API shortcut the setup project uses.

const ROLES = ["admin", "alice", "bob"] as const;

test.describe("iam auth — credential login", () => {
  for (const role of ROLES) {
    test(`${role} signs in and reaches the authenticated console`, async ({
      page,
    }) => {
      const login = new LoginPage(page);
      await login.goto();
      await login.signIn(role, role);

      await expect(page).toHaveURL(/\/notes/, { timeout: 25000 });
      // The user menu is authenticated-only chrome; it confirms the session
      // regardless of how many records the role's REBAC scope exposes.
      await expect(
        page.getByRole("button", { name: "User menu" }),
      ).toBeVisible({ timeout: 25000 });
    });
  }
});

test.describe("iam auth — OAuth sign-in", () => {
  // The demo OIDC connection (seeded in the demo resource tier) surfaces a
  // "Continue with <provider>" button; clicking it starts the provider redirect
  // with a same-site redirect_uri and PKCE. The external hop is blocked so the
  // test never leaves.
  test("the provider button starts an OIDC redirect with PKCE", async ({
    page,
  }) => {
    await page.route(/accounts\.google\.com/, (route) => route.abort());
    const login = new LoginPage(page);
    await login.goto();

    const provider = page.getByRole("button", { name: /continue with/i });
    await provider.waitFor({ state: "visible", timeout: 15000 });

    const [request] = await Promise.all([
      page.waitForRequest(/accounts\.google\.com/, { timeout: 10000 }),
      provider.click(),
    ]);
    const url = request.url();
    expect(url).toContain("redirect_uri=");
    expect(url).toContain(encodeURIComponent("/login/callback"));
    expect(url).toContain("code_challenge="); // PKCE
  });
});

test.describe("iam auth — OAuth callback", () => {
  // A malformed provider return (no code/state) must not hang: the callback
  // shows an error and a way back to sign-in, with no provider round-trip.
  test("reports an error and offers a way back when params are missing", async ({
    page,
  }) => {
    await page.goto("/login/callback");

    await expect(page.getByText("Could not sign in")).toBeVisible({
      timeout: 15000,
    });
    // Base UI's Button keeps role="button" even when it renders an anchor.
    await expect(
      page.getByRole("button", { name: "Back to sign in" }),
    ).toBeVisible();
  });
});
