// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, type AppRuntime } from "@angee/sdk";

import {
  AUTH_LOGIN_CARD_FOOTER_SLOT,
  LoginPage,
} from "./LoginPage";

vi.mock("@angee/logo-react", () => ({
  AngeeLogo: (props: { width?: number; height?: number }) => (
    <svg aria-label="Angee" width={props.width} height={props.height} />
  ),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useLoginWithPassword: () => ({
      fetching: false,
      login: vi.fn(async () => ({ ok: true })),
    }),
  };
});

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
        <LoginPage />
      </Wrapper>,
    );

    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeTruthy();
    expect(screen.getByText("Demo users")).toBeTruthy();
  });
});
