// @vitest-environment happy-dom
import { renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, test } from "vitest";

import { AuthProvider, useAuth, type AuthState } from "./auth";

function wrapperFor(auth: Partial<AuthState>) {
  return ({ children }: { children: ReactNode }) =>
    createElement(AuthProvider, { auth, children });
}

describe("useAuth", () => {
  test("defaults to an anonymous state without a provider", () => {
    const { result } = renderHook(() => useAuth());
    expect(result.current.status).toBe("anonymous");
    expect(result.current.user).toBeNull();
    expect(result.current.hasRole("admin")).toBe(false);
  });

  test("exposes the authenticated user and roles", () => {
    const wrapper = wrapperFor({
      status: "authenticated",
      user: { id: "1", name: "Ada", roles: ["admin", "editor"] },
    });
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.user?.name).toBe("Ada");
    expect(result.current.hasRole("editor")).toBe(true);
    expect(result.current.hasRole("viewer")).toBe(false);
  });
});
