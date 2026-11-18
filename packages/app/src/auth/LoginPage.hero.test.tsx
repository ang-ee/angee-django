// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, containersFromChildren, type AppRuntime, type LoginHeroContent } from "@angee/ui/runtime";

import { LOGIN_CONTAINERS, LoginPage, type LoginPageProps } from "./LoginPage";

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../providers/auth", () => ({
  useLoginWithPassword: () => ({ fetching: false, login: vi.fn(async () => ({ ok: true })) }),
}));
vi.mock("@angee/logo-react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/logo-react")>()),
  AngeeLogoCube: () => <svg />,
}));

const brandedHost: Partial<AppRuntime> = {
  brand: { name: "Notebook", mark: "notebook-mark" },
  icons: { "notebook-mark": () => <svg /> },
};
const PAGE_BACKDROP = "/page-backdrop.webp";
const BRAND_BACKDROP = "/brand-backdrop.webp";

function BrandHero() {
  return <aside>Brand hero</aside>;
}

function withHero(runtime: Partial<AppRuntime>, hero?: LoginHeroContent): Partial<AppRuntime> {
  return hero === undefined ? runtime : {
    ...runtime,
    containers: containersFromChildren(LOGIN_CONTAINERS, { "auth.login#hero": { "brand.login-hero": { content: hero } } }),
  };
}

function login(runtime: Partial<AppRuntime>, props: LoginPageProps = {}) {
  return render(
    <AppRuntimeProvider runtime={runtime}>
      <LoginPage backgroundImageUrl={PAGE_BACKDROP} {...props} />
    </AppRuntimeProvider>,
  );
}

/** The backdrop's image layers (the atmosphere's glow is a gradient, not an image). */
function backdropImages(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>("div"))
    .map((element) => element.style.backgroundImage)
    .filter((image) => image.startsWith("url("));
}

/** The atmosphere's grid and glow layers over a full-bleed backdrop. */
function atmosphereGlow(container: HTMLElement): boolean {
  return Array.from(container.querySelectorAll<HTMLElement>("div"))
    .some((element) => element.style.backgroundImage.startsWith("linear-gradient"));
}

/** The framework hero's wandering cube, which only the framework hero's atmosphere shows. */
function wanderingCube(container: HTMLElement): Element | null {
  return container.querySelector(".mix-blend-screen");
}

function heroCell(text: string): HTMLElement {
  return screen.getByText(text).parentElement!;
}

describe("login hero contribution", () => {
  test("a contributed hero renders on a branded host at every width, beside the card by default", () => {
    const { container } = login(withHero(brandedHost, { render: BrandHero }));
    expect(heroCell("Brand hero").className).not.toContain("hidden");
    expect(backdropImages(container)).toEqual([]);
    expect(wanderingCube(container)).toBeNull();
  });

  test("a full-bleed contribution brings its own backdrop image, never the page's", () => {
    const { container } = login(withHero(brandedHost, {
      render: BrandHero, presentation: "full-bleed", backgroundImageUrl: BRAND_BACKDROP,
    }));
    expect(screen.getByText("Brand hero")).toBeTruthy();
    expect(backdropImages(container)).toEqual([`url("${BRAND_BACKDROP}")`]);
    // The framework's atmosphere and cube are the unbranded identity.
    expect(atmosphereGlow(container)).toBe(false);
    expect(wanderingCube(container)).toBeNull();
  });

  test("a contribution replaces the framework hero on an unbranded host", () => {
    const { container } = login(withHero({ brand: null }, { render: BrandHero }));
    expect(screen.getByText("Brand hero")).toBeTruthy();
    expect(wanderingCube(container)).toBeNull();
    expect(backdropImages(container)).toEqual([]);
  });

  test("an explicit hero prop wins over the contribution, and null removes it", () => {
    const runtime = withHero(brandedHost, { render: BrandHero, presentation: "full-bleed", backgroundImageUrl: BRAND_BACKDROP });
    const mounted = login(runtime, { hero: <aside>Page hero</aside> });
    expect(screen.getByText("Page hero")).toBeTruthy();
    expect(screen.queryByText("Brand hero")).toBeNull();
    // The page's own hero takes the page's presentation: split unless it asks for full-bleed.
    expect(backdropImages(mounted.container)).toEqual([]);
    mounted.unmount();

    login(runtime, { hero: null });
    expect(screen.queryByText("Brand hero")).toBeNull();
    expect(screen.queryByText("Page hero")).toBeNull();
  });

  test("a branded host's own hero reaches the full-bleed backdrop and shows at every width", () => {
    const { container } = login(brandedHost, { hero: <aside>Page hero</aside>, presentation: "full-bleed" });
    expect(heroCell("Page hero").className).not.toContain("hidden");
    expect(backdropImages(container)).toEqual([`url("${PAGE_BACKDROP}")`]);
    expect(wanderingCube(container)).toBeNull();
  });

  test("without a hero or contribution, an unbranded host keeps the full-bleed framework hero from lg up", () => {
    const { container } = login({ brand: null });
    expect(wanderingCube(container)).toBeTruthy();
    expect(atmosphereGlow(container)).toBe(true);
    expect(backdropImages(container)).toEqual([`url("${PAGE_BACKDROP}")`]);
    const frameworkCell = container.querySelector<HTMLElement>("[aria-hidden='true'].relative");
    expect(frameworkCell?.className).toContain("hidden");
    expect(frameworkCell?.className).toContain("lg:block");
  });
});
