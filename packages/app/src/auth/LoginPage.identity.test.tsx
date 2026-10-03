// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, containersFromChildren, createAngeeI18nInstance, type AppRuntime } from "@angee/ui/runtime";

import { LOGIN_CONTAINERS, LoginPage, type LoginPageProps } from "./LoginPage";

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../providers/auth", () => ({
  useLoginWithPassword: () => ({ fetching: false, login: vi.fn(async () => ({ ok: true })) }),
}));
vi.mock("@angee/logo-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/logo-react")>()),
  AngeeLogoCube: () => <svg data-testid="theme-cube" />,
}));
vi.mock("@angee/ui/theme", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui/theme")>()),
  ThemeLogo: () => <svg data-testid="theme-logo" />,
  useThemeLogoChoice: () => "theme",
}));
afterEach(cleanup);

const branded: Partial<AppRuntime> = {
  brand: { name: "Notebook", mark: "notebook-mark" },
  icons: { "notebook-mark": () => <svg data-testid="runtime-mark" /> },
};

function login(props: LoginPageProps = {}, runtime: Partial<AppRuntime> = branded) {
  return render(<AppRuntimeProvider runtime={runtime}><LoginPage showAtmosphere={false} {...props} /></AppRuntimeProvider>);
}

describe("login identity precedence", () => {
  test("the explicit brand node replaces runtime identity", () => {
    login({ brand: <span>Custom identity</span> });
    expect(screen.getByText("Custom identity")).toBeTruthy();
    expect(screen.queryByText("Notebook", { exact: true })).toBeNull();
    expect(screen.queryByTestId("runtime-mark")).toBeNull();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
  });

  test("runtime identity supplies the mark and interpolated name without the default hero", () => {
    login();
    expect(screen.getByText("Notebook", { exact: true })).toBeTruthy();
    expect(screen.getByTestId("runtime-mark")).toBeTruthy();
    expect(screen.getByText("Use your Notebook account credentials.")).toBeTruthy();
    expect(screen.queryByTestId("theme-cube")).toBeNull();
    expect(screen.queryByTestId("theme-logo")).toBeNull();
  });

  test("unbranded identity uses the theme logo and translated product name", () => {
    login({ hero: null }, {
      brand: null,
      i18n: createAngeeI18nInstance({ ui: { "auth.productName": "Shared workspace" } }),
    });
    expect(screen.getByText("Shared workspace")).toBeTruthy();
    expect(screen.getByTestId("theme-cube")).toBeTruthy();
    expect(screen.queryByTestId("runtime-mark")).toBeNull();
  });

  test("an explicit hero is retained for a branded login", () => {
    login({ hero: <aside>Welcome panel</aside> });
    expect(screen.getByText("Welcome panel")).toBeTruthy();
    expect(screen.getByText("Notebook", { exact: true })).toBeTruthy();
  });

  test("a null card header keeps the branded identity and removes the default heading", () => {
    login({ cardHeader: null });
    expect(screen.getByText("Notebook", { exact: true })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Sign in" })).toBeNull();
  });

  test("methods use auth.login#method by default and allow replacement or suppression", () => {
    const runtime = { ...branded, containers: containersFromChildren(LOGIN_CONTAINERS, {
      "auth.login#method": { "oidc.sso": { content: <button>Slot sign-in</button> } },
    }) };
    const page = (methods?: LoginPageProps["methods"]) => <AppRuntimeProvider runtime={runtime}>
      <LoginPage methods={methods} showAtmosphere={false} />
    </AppRuntimeProvider>;
    const mounted = render(page());
    expect(screen.getByRole("button", { name: "Slot sign-in" })).toBeTruthy();
    mounted.rerender(page(<button>Replacement sign-in</button>));
    expect(screen.getByRole("button", { name: "Replacement sign-in" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Slot sign-in" })).toBeNull();
    mounted.rerender(page(null));
    expect(screen.queryByRole("button", { name: "Replacement sign-in" })).toBeNull();
    expect(screen.queryByText("or use password")).toBeNull();
  });
});
