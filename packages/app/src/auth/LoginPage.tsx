import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AngeeLogoCube, PRESETS } from "@angee/logo-react";
import "@angee/logo-react/style.css";
import { useContainer, useRuntimeBrand, type CoreContainer, type LoginHeroContent } from "@angee/ui/runtime";
import { Glyph } from "@angee/ui/chrome/Glyph";
import { AnimatedThemeLogo, ThemeLogo } from "@angee/ui/theme";

import { useUiT } from "@angee/ui/i18n";
import { cn } from "@angee/ui/lib/cn";
import { ContainerOutlet, containerHasContent } from "@angee/ui/lib/container-outlet";
import { useSignInRedirect } from "./sign-in-redirect";
import { UsernamePasswordForm } from "./UsernamePasswordForm";

/** The login page's containers: sign-in methods, password help and its one hero.
 * @kind contribution
 */
export const LOGIN_CONTAINERS: readonly CoreContainer[] = [
  { address: "auth.login#method" },
  { address: "auth.login#password-help" },
  { address: "auth.login#hero", unique: "address" },
];

/** Refuse an `auth.login#hero` child whose content `LoginPage` cannot render. */
export function validateLoginHero(content: unknown, id: string): asserts content is LoginHeroContent {
  const hero = typeof content === "object" && content !== null ? content as Record<string, unknown> : null;
  const render = hero?.render;
  if (!hero || (typeof render !== "function" && (typeof render !== "object" || render === null))) {
    throw new Error(`Login hero "${id}" needs a render component.`);
  }
  if (hero.presentation !== undefined && hero.presentation !== "full-bleed") {
    throw new Error(`Login hero "${id}" names presentation "${String(hero.presentation)}"; a login hero is "full-bleed" or unset.`);
  }
  if (hero.backgroundImageUrl !== undefined && typeof hero.backgroundImageUrl !== "string") {
    throw new Error(`Login hero "${id}" needs a string backgroundImageUrl.`);
  }
}

const HERO_SLIDE_KEYS = ["intent", "agentNative", "composable"] as const;
const LOGIN_BACKDROP_FADE_MS = 1_000;
const LOGIN_LOGO_PRESET = PRESETS.gold;
const LOGIN_TEAL = "#14b8a6";
const LOGIN_CUBE_DARK = "#08111f";

export interface LoginPageProps {
  brand?: ReactNode;
  /** Undefined uses contributed login methods; null suppresses them. */
  methods?: ReactNode | null;
  redirectTo?: string;
  footer?: ReactNode;
  /**
   * Undefined uses the `auth.login#hero` contribution, else the framework hero
   * on an unbranded host; `null` removes the hero; a node replaces it. A host's
   * hero shows at every width; the framework's own slides show from `lg` up.
   */
  hero?: ReactNode | null;
  /**
   * How the page's own hero (the `hero` prop) sits: `"full-bleed"` puts a
   * backdrop behind the whole page with the card floating over it, also with no
   * hero; unset gives the hero its own column beside the card, stacked above it
   * on small screens. The framework hero is always full-bleed, and a
   * contributed hero brings its own presentation.
   */
  presentation?: "full-bleed";
  /** `undefined` renders the default card header, `null` leaves only `brand`, a node replaces it. */
  cardHeader?: ReactNode | null;
  /** `undefined` renders the `auth.login#password-help` children, `null` suppresses them, a node replaces them. */
  passwordHelp?: ReactNode | null;
  /** The framework's grid and glow over a full-bleed backdrop, with its cube beside the framework hero; on by default only with the framework hero. */
  showAtmosphere?: boolean;
  /** The full-bleed backdrop's image for the page's own hero (the `hero` prop or the framework hero); a contributed hero brings its own. */
  backgroundImageUrl?: string;
}

/**
 * @kind page
 */
export function LoginPage({
  brand,
  methods,
  redirectTo = "/",
  footer,
  hero,
  presentation,
  cardHeader,
  passwordHelp,
  showAtmosphere,
  backgroundImageUrl,
}: LoginPageProps): ReactNode {
  const onSuccess = useSignInRedirect(redirectTo);
  const t = useUiT();
  const runtimeBrand = useRuntimeBrand();
  const branded = brand != null || runtimeBrand !== null;
  const name = runtimeBrand?.name ?? t("auth.productName");
  const identity = brand ?? <LoginIdentity name={name} />;
  const methodChildren = useContainer("auth.login#method");
  const passwordHelpChildren = useContainer("auth.login#password-help");
  const heroChildren = useContainer<LoginHeroContent>("auth.login#hero");
  const cardFooter = footer ?? null;
  const formPasswordHelp = passwordHelp === null
    ? null
    : (passwordHelp
      ?? (containerHasContent(passwordHelpChildren)
        ? <ContainerOutlet entries={passwordHelpChildren} />
        : null));
  const loginMethods = methods === undefined
    ? containerHasContent(methodChildren) ? <ContainerOutlet entries={methodChildren} /> : null
    : methods;
  // An explicit hero wins; else the contribution, with its own presentation and image;
  // else the framework hero, which only an unbranded host shows.
  const contributed = hero === undefined ? heroChildren[0]?.content : undefined;
  const frameworkHero = hero === undefined && contributed === undefined && runtimeBrand === null;
  const ContributedHero = contributed?.render;
  const resolvedHero = frameworkHero
    ? <LoginBrandPanel brand={brand} />
    : ContributedHero ? <ContributedHero /> : hero ?? null;
  const showHero = resolvedHero !== null;
  const fullBleed = frameworkHero
    || (contributed ? contributed.presentation : presentation) === "full-bleed";
  const backdropImageUrl = contributed ? contributed.backgroundImageUrl : backgroundImageUrl;
  const atmosphere = showAtmosphere ?? frameworkHero;

  return (
    <main
      className={cn(
        "relative min-h-screen overflow-hidden text-fg",
        fullBleed
          ? showHero
            ? "grid items-center gap-8 px-5 py-8 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(25rem,31rem)] lg:gap-12 lg:px-12 xl:px-16"
            : "grid place-items-center px-5 py-8 sm:px-8"
          : showHero
            ? "grid lg:grid-cols-[minmax(0,1fr)_minmax(28rem,34rem)]"
            : "grid place-items-center px-5 py-8",
        fullBleed ? "bg-n-950" : "bg-canvas",
      )}
    >
      {fullBleed ? (
        <LoginVisualBackdrop
          imageUrl={backdropImageUrl}
          showAtmosphere={atmosphere}
        />
      ) : null}
      {frameworkHero && atmosphere ? <WanderingCube /> : null}
      {showHero ? (
        <div
          className={cn(
            "relative z-10",
            frameworkHero
              ? "hidden min-h-[32rem] lg:block"
              : fullBleed ? "lg:min-h-[32rem]" : "lg:min-h-screen",
          )}
          aria-hidden={frameworkHero ? "true" : undefined}
        >
          {resolvedHero}
        </div>
      ) : null}
      <section
        className={cn(
          "relative z-10 flex items-center justify-center",
          fullBleed
            ? showHero
              // A host's hero stacks above the card on small screens; the framework's shows from lg.
              ? cn("lg:justify-end", frameworkHero ? "min-h-[calc(100vh-4rem)]" : "lg:min-h-[calc(100vh-4rem)]")
              : "w-full"
            : showHero
              ? "border-t border-border-subtle bg-sheet px-5 py-8 sm:px-8 lg:min-h-screen lg:border-l lg:border-t-0"
              : "min-h-0 w-full max-w-[30rem] rounded-8 border border-border bg-sheet px-5 py-8 shadow-lg sm:px-8",
        )}
      >
        <div
          className={cn(
            "w-full max-w-[26rem]",
            fullBleed
              ? "rounded-8 border border-n-0/20 bg-sheet/94 p-6 shadow-2xl shadow-n-950/35 backdrop-blur-xl ring-1 ring-n-0/15 sm:p-8"
              : null,
          )}
        >
          {cardHeader === null ? (
            branded ? <div className="mb-8">{identity}</div> : null
          ) : cardHeader ? (
            <>
              <div className="mb-7">{identity}</div>
              <div className="mb-7">{cardHeader}</div>
            </>
          ) : (
            <DefaultCardHeader brand={brand} branded={branded} name={name} />
          )}
          {loginMethods ? (
            // A contributed method may render nothing (a sign-in provider with no
            // configured connection), so the divider shows only beside rendered methods.
            <div className="mb-5 [&:has(>[data-login-methods]:empty)]:hidden">
              <div data-login-methods="">{loginMethods}</div>
              <div className="mt-5 flex items-center gap-3 text-12 text-fg-muted">
                <span className="h-px flex-1 bg-border-subtle" aria-hidden />
                <span>{t("auth.orUsePassword")}</span>
                <span className="h-px flex-1 bg-border-subtle" aria-hidden />
              </div>
            </div>
          ) : null}
          <UsernamePasswordForm
            onSuccess={onSuccess}
            passwordHelp={formPasswordHelp}
          />
          {cardFooter ? (
            <div className="mt-6 rounded-6 border border-border-subtle bg-inset/70 px-4 py-3">
              {cardFooter}
            </div>
          ) : null}
        </div>
      </section>
    </main>
  );
}

function LoginBrandPanel({
  brand,
}: {
  brand?: ReactNode;
}): ReactNode {
  const { isSwitching, slide } = useHeroSlide();

  return (
    <section className="relative flex h-full min-h-[32rem] flex-col justify-end text-n-0">
      {brand ? <div className="absolute left-0 top-0 z-10">{brand}</div> : null}
      <div className="relative z-10 pb-6">
        <div className="w-full max-w-xl">
          <div
            className={cn(
              "min-h-[17rem] transition-all duration-500 ease-out motion-reduce:transform-none motion-reduce:opacity-100 motion-reduce:transition-none",
              isSwitching
                ? "translate-y-3 opacity-0 blur-[2px]"
                : "translate-y-0 opacity-100 blur-0",
            )}
          >
            <p className="mb-5 text-12 font-semibold uppercase tracking-[0.18em] text-n-0/72">
              {slide.eyebrow}
            </p>
            <h1 className="max-w-[13ch] text-5xl font-semibold leading-[1.02] text-n-0 xl:text-6xl">
              {slide.headline}
            </h1>
            <p className="mt-5 max-w-lg text-base leading-7 text-n-100/88">
              {slide.body}
            </p>
          </div>
          <div className="mt-7 flex gap-2" aria-hidden="true">
            {HERO_SLIDE_KEYS.map((candidate) => (
              <span
                key={candidate}
                className={cn(
                  "h-1.5 rounded-full transition-all duration-500 motion-reduce:transition-none",
                  candidate === slide.key
                    ? "w-8 bg-n-0/78"
                    : "w-1.5 bg-n-0/32",
                )}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

interface HeroSlide {
  key: (typeof HERO_SLIDE_KEYS)[number];
  eyebrow: string;
  headline: string;
  body: string;
}

function useHeroSlide(): {
  isSwitching: boolean;
  slide: HeroSlide;
} {
  const t = useUiT();
  const [index, setIndex] = useState(0);
  const [isSwitching, setIsSwitching] = useState(false);

  useEffect(() => {
    if (HERO_SLIDE_KEYS.length < 2 || prefersReducedMotion()) return undefined;

    let timeout: number | undefined;
    const interval = window.setInterval(() => {
      setIsSwitching(true);
      timeout = window.setTimeout(() => {
        setIndex((current) => (current + 1) % HERO_SLIDE_KEYS.length);
        setIsSwitching(false);
      }, 320);
    }, 5600);

    return () => {
      window.clearInterval(interval);
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, []);

  const key = HERO_SLIDE_KEYS[index] ?? HERO_SLIDE_KEYS[0];
  return {
    isSwitching,
    slide: {
      key,
      eyebrow: t(`auth.hero.${key}.eyebrow`),
      headline: t(`auth.hero.${key}.headline`),
      body: t(`auth.hero.${key}.body`),
    },
  };
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function LoginVisualBackdrop({
  imageUrl,
  showAtmosphere,
}: {
  imageUrl?: string;
  showAtmosphere: boolean;
}): ReactNode {
  const {
    currentImageUrl,
    currentImageVisible,
    previousImageUrl,
  } = useFadingBackdropImage(imageUrl);

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      <div className="absolute inset-0 bg-[linear-gradient(135deg,#07111f,#123129_46%,#08101d)]" />
      {previousImageUrl ? (
        <LoginBackdropImageLayer imageUrl={previousImageUrl} />
      ) : null}
      {currentImageUrl ? (
        <LoginBackdropImageLayer
          imageUrl={currentImageUrl}
          className={cn(
            "transition-opacity duration-1000 ease-out motion-reduce:transition-none",
            currentImageVisible ? "opacity-100" : "opacity-0",
          )}
        />
      ) : null}
      <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(4,9,18,0.72)_0%,rgba(4,9,18,0.18)_46%,rgba(4,9,18,0.48)_100%)]" />
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(4,9,18,0.08)_0%,rgba(4,9,18,0.12)_44%,rgba(4,9,18,0.58)_100%)]" />
      {showAtmosphere ? (
        <>
          <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(255,255,255,0.08)_1px,transparent_1px),linear-gradient(rgba(255,255,255,0.06)_1px,transparent_1px)] bg-[size:56px_56px] opacity-[0.24] [mask-image:linear-gradient(90deg,black,transparent_74%)]" />
          <div
            className="absolute inset-0"
            style={{
              backgroundImage:
                `linear-gradient(118deg, color-mix(in srgb, ${LOGIN_TEAL} 16%, transparent) 0%, transparent 42%), `
                + `linear-gradient(318deg, color-mix(in srgb, ${LOGIN_LOGO_PRESET.colors.top} 12%, transparent) 0%, transparent 36%)`,
            }}
          />
        </>
      ) : null}
    </div>
  );
}

function useFadingBackdropImage(imageUrl?: string): {
  currentImageUrl?: string;
  currentImageVisible: boolean;
  previousImageUrl?: string;
} {
  const currentImageUrlRef = useRef(imageUrl);
  const [currentImageUrl, setCurrentImageUrl] = useState(imageUrl);
  const [previousImageUrl, setPreviousImageUrl] = useState<string | undefined>();
  const [currentImageVisible, setCurrentImageVisible] = useState(true);

  useEffect(() => {
    if (imageUrl === currentImageUrlRef.current) return undefined;

    setPreviousImageUrl(currentImageUrlRef.current);
    currentImageUrlRef.current = imageUrl;
    setCurrentImageUrl(imageUrl);
    setCurrentImageVisible(false);

    const frame = window.requestAnimationFrame(() => {
      setCurrentImageVisible(true);
    });
    const timeout = window.setTimeout(() => {
      setPreviousImageUrl(undefined);
    }, LOGIN_BACKDROP_FADE_MS);

    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timeout);
    };
  }, [imageUrl]);

  return { currentImageUrl, currentImageVisible, previousImageUrl };
}

function LoginBackdropImageLayer({
  className,
  imageUrl,
}: {
  className?: string;
  imageUrl: string;
}): ReactNode {
  return (
    <div
      className={cn("absolute inset-0 bg-cover bg-center bg-no-repeat", className)}
      style={{ backgroundImage: `url("${imageUrl}")` }}
    />
  );
}

function WanderingCube(): ReactNode {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute left-0 top-0 z-[1] hidden h-72 w-72 opacity-70 mix-blend-screen lg:block"
    >
      <AngeeLogoCube
        size={68}
        gap={2}
        leftColor={LOGIN_TEAL}
        rightColor={LOGIN_LOGO_PRESET.colors.top}
        baseDark={LOGIN_CUBE_DARK}
        animationSpeed={24}
        animationType="rotate-slide"
        wander
        wanderSpeed={92}
      />
    </div>
  );
}

function LoginIdentity({
  name,
  brand: override,
  header = false,
  children,
}: { name: string; brand?: ReactNode; header?: boolean; children?: ReactNode }): ReactNode {
  const brand = useRuntimeBrand();
  if (header && override == null && brand === null) {
    return (
      <div className="mb-8 grid grid-cols-[4rem_minmax(0,1fr)] items-start gap-4 text-left">
        <div className="pointer-events-none grid size-16 p-2" aria-hidden="true">
          <AnimatedThemeLogo size={48} />
        </div>
        <div className="min-w-0 pt-1">
          <p className="mb-2 text-base font-semibold leading-none text-fg">{name}</p>
          {children}
        </div>
      </div>
    );
  }
  const identity = override ?? (
    <div className="inline-flex items-center gap-3 text-fg">
      <span className="grid size-9 place-content-center rounded-6 border border-border-subtle bg-sheet">
        {brand ? <Glyph name={brand.mark} size={22} />
          : <ThemeLogo aria-hidden="true" size={22} width={22} height={22} />}
      </span>
      <span className="text-xl font-semibold">{name}</span>
    </div>
  );
  return header ? <div className="mb-8"><div className="mb-6">{identity}</div>{children}</div> : identity;
}

function DefaultCardHeader({ brand, branded, name }: { brand?: ReactNode; branded: boolean; name: string }): ReactNode {
  const t = useUiT();
  return (
    <LoginIdentity brand={brand} name={name} header>
      <h1 className="text-28 font-semibold leading-tight text-fg">{t("auth.signIn")}</h1>
      <p className="mt-2 text-sm text-fg-muted">
        {t(branded ? "auth.signInSubtextBranded" : "auth.signInSubtext", { name })}
      </p>
    </LoginIdentity>
  );
}
