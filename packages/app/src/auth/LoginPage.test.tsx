// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, containersFromChildren, type AppRuntime } from "@angee/ui/runtime";

import { LOGIN_CONTAINERS, LoginPage } from "./LoginPage";

vi.mock("@angee/logo-react", async (importOriginal) => {
  const { PRESETS } = await importOriginal<typeof import("@angee/logo-react")>();
  return {
    AngeeLogo: (props: { width?: number; height?: number }) => (
      <svg aria-label="Angee" width={props.width} height={props.height} />
    ),
    AngeeLogoCube: () => <div data-testid="angee-logo-cube" />,
    PRESETS,
  };
});

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("../providers/auth", () => ({
  useLoginWithPassword: () => ({
    fetching: false,
    login: vi.fn(async () => ({ ok: true })),
  }),
}));

afterEach(cleanup);

function wrapperFor(runtime: Partial<AppRuntime>) {
  return ({ children }: { children: ReactNode }) =>
    createElement(AppRuntimeProvider, { runtime, children });
}

describe("LoginPage", () => {
  test("renders the card footer from the footer prop", () => {
    render(
      <AppRuntimeProvider runtime={{}}>
        <LoginPage showAtmosphere={false} footer={<p>Demo users</p>} />
      </AppRuntimeProvider>,
    );

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByTestId("angee-logo-cube")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Forgot your password?" })).toBeNull();
    expect(screen.getByText("Demo users")).toBeTruthy();
  });

  test("renders password help from auth.login#password-help", () => {
    const Wrapper = wrapperFor({
      containers: containersFromChildren(LOGIN_CONTAINERS, {
        "auth.login#password-help": { "iam.recover-access": { content: <button type="button">Recover access</button> } },
      }),
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
