// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, type AppRuntime } from "@angee/ui/runtime";

import {
  AUTH_LOGIN_CARD_FOOTER_SLOT,
  AUTH_LOGIN_PASSWORD_HELP_SLOT,
  LoginPage,
} from "./LoginPage";

vi.mock("@angee/logo-react", () => ({
  AngeeLogo: (props: { width?: number; height?: number }) => (
    <svg aria-label="Angee" width={props.width} height={props.height} />
  ),
  AngeeLogoCube: () => <div data-testid="angee-logo-cube" />,
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useLoginWithPassword: () => ({
      fetching: false,
      login: vi.fn(async () => ({ ok: true })),
    }),
  };
});

afterEach(cleanup);

function wrapperFor(runtime: Partial<AppRuntime>) {
  return ({ children }: { children: ReactNode }) =>
    createElement(AppRuntimeProvider, { runtime, children });
}

describe("LoginPage", () => {
  test("renders the card footer from the login slot", () => {
    const Wrapper = wrapperFor({
      slots: [
        {
          slot: AUTH_LOGIN_CARD_FOOTER_SLOT,
          id: "demo-users",
          content: <p>Demo users</p>,
        },
      ],
    });

    render(
      <Wrapper>
        <LoginPage showAtmosphere={false} />
      </Wrapper>,
    );

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByTestId("angee-logo-cube")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Forgot your password?" })).toBeTruthy();
    expect(screen.getByText("Demo users")).toBeTruthy();
  });

  test("replaces the default password help from the login slot", () => {
    const Wrapper = wrapperFor({
      slots: [
        {
          slot: AUTH_LOGIN_PASSWORD_HELP_SLOT,
          id: "recover-access",
          content: <button type="button">Recover access</button>,
        },
      ],
    });

    render(
      <Wrapper>
        <LoginPage />
      </Wrapper>,
    );

    expect(screen.getByRole("button", { name: "Recover access" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Forgot your password?" })).toBeNull();
  });
});
