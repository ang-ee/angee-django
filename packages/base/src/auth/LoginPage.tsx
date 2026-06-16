import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useNavigate } from "@tanstack/react-router";
import { AngeeLogo, AngeeLogoCube } from "@angee/logo-react";
import "@angee/logo-react/style.css";
import { useSlot } from "@angee/sdk";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { SlotOutlet, slotEntriesHaveContent } from "../lib/slot-outlet";
import { safeRedirectPath } from "./safe-redirect";
import { UsernamePasswordForm } from "./UsernamePasswordForm";
import { Button } from "../ui/button";

export const AUTH_LOGIN_METHOD_SLOT = "auth.login.method";
export const AUTH_LOGIN_CARD_FOOTER_SLOT = "auth.login.card-footer";
export const AUTH_LOGIN_PAGE_FOOTER_SLOT = "auth.login.page-footer";
export const AUTH_LOGIN_PASSWORD_HELP_SLOT = "auth.login.password-help";

const HERO_SLIDE_KEYS = ["intent", "agentNative", "composable"] as const;
const LOGIN_BACKDROP_FADE_MS = 1_000;

export interface LoginPageProps {
  brand?: ReactNode;
  redirectTo?: string;
  footer?: ReactNode;
  hero?: ReactNode | null;
  cardHeader?: ReactNode | null;
  passwordHelp?: ReactNode | null;
  showAtmosphere?: boolean;
  backgroundImageUrl?: string;
}

export function LoginPage({
  brand,
  redirectTo = "/",
  footer,
  hero,
  cardHeader,
  passwordHelp,
  showAtmosphere,
  backgroundImageUrl,
}: LoginPageProps): ReactNode {
  const navigate = useNavigate();
  const onSuccess = useCallback(() => {
    const next =
      typeof window === "undefined"
        ? null
        : new URLSearchParams(window.location.search).get("next");
    const target = safeRedirectPath(next) ?? safeRedirectPath(redirectTo) ?? "/";
    if (typeof window === "undefined") {
      void navigate({ to: target });
      return;
    }
    window.location.assign(target);
  }, [navigate, redirectTo]);
  const t = useBaseT();
  const methodSlot = useSlot(AUTH_LOGIN_METHOD_SLOT);
  const cardFooterSlot = useSlot(AUTH_LOGIN_CARD_FOOTER_SLOT);
  const pageFooterSlot = useSlot(AUTH_LOGIN_PAGE_FOOTER_SLOT);
  const passwordHelpSlot = useSlot(AUTH_LOGIN_PASSWORD_HELP_SLOT);
  const cardFooter = footer
    ?? (slotEntriesHaveContent(cardFooterSlot)
      ? <SlotOutlet entries={cardFooterSlot} />
      : null);
  const pageFooter = slotEntriesHaveContent(pageFooterSlot)
    ? <SlotOutlet entries={pageFooterSlot} />
    : null;
  const formPasswordHelp = passwordHelp === null
    ? null
    : (passwordHelp
      ?? (slotEntriesHaveContent(passwordHelpSlot)
        ? <SlotOutlet entries={passwordHelpSlot} />
        : <DefaultPasswordHelp />));
  const loginMethods = slotEntriesHaveContent(methodSlot)
    ? <SlotOutlet entries={methodSlot} />
    : null;
  const defaultHero = hero === undefined;
  const defaultAtmosphere = showAtmosphere ?? true;
  const resolvedHero =
    defaultHero ? <LoginBrandPanel brand={brand} /> : hero;
  const showHero = resolvedHero !== null;

  return (
    <main
      className={cn(
        "relative min-h-screen overflow-hidden text-fg",
        showHero && defaultHero
          ? "grid items-center gap-8 px-5 py-8 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(25rem,31rem)] lg:gap-12 lg:px-12 xl:px-16"
          : showHero
            ? "grid lg:grid-cols-[minmax(0,1fr)_minmax(28rem,34rem)]"
            : "grid place-items-center px-5 py-8",
        showHero && defaultHero ? "bg-n-950" : "bg-canvas",
      )}
    >
      {showHero && defaultHero ? (
        <LoginVisualBackdrop
          imageUrl={backgroundImageUrl}
          showAtmosphere={defaultAtmosphere}
        />
      ) : null}
      {showHero && defaultHero && defaultAtmosphere ? <WanderingCube /> : null}
      {showHero ? (
        <div
          className={cn(
            "relative z-10 hidden lg:block",
            defaultHero ? "min-h-[32rem]" : "min-h-screen",
          )}
          aria-hidden={defaultHero ? "true" : undefined}
        >
          {resolvedHero}
        </div>
      ) : null}
      <section
        className={cn(
          "relative z-10 flex items-center justify-center",
          showHero && defaultHero
            ? "min-h-[calc(100vh-4rem)] lg:justify-end"
            : showHero
              ? "min-h-screen border-l border-border-subtle bg-sheet px-5 py-8 sm:px-8"
              : "min-h-0 w-full max-w-[30rem] rounded-lg border border-border bg-sheet px-5 py-8 shadow-lg sm:px-8",
        )}
      >
        <div
          className={cn(
            "w-full max-w-[26rem]",
            showHero
              ? defaultHero
                ? "rounded-lg border border-n-0/20 bg-sheet/94 p-6 shadow-2xl shadow-n-950/35 backdrop-blur-xl ring-1 ring-n-0/15 sm:p-8"
                : null
              : null,
          )}
        >
          {cardHeader === null ? (
            brand ? <div className="mb-8">{brand}</div> : null
          ) : cardHeader ? (
            <>
              <div className="mb-7">{brand ?? <AngeeIdentity />}</div>
              <div className="mb-7">{cardHeader}</div>
            </>
          ) : (
            <DefaultCardHeader brand={brand} />
          )}
          {loginMethods ? (
            <div className="mb-5">
              {loginMethods}
              <div className="mt-5 flex items-center gap-3 text-xs text-fg-muted">
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
            <div className="mt-6 rounded-md border border-border-subtle bg-inset/70 px-4 py-3">
              {cardFooter}
            </div>
          ) : null}
          {pageFooter ? (
            <div className="mt-8 text-center text-xs text-fg-muted">
              {pageFooter}
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
            <p className="mb-5 text-xs font-semibold uppercase tracking-[0.18em] text-n-0/72">
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
  const t = useBaseT();
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
          <div className="absolute inset-0 bg-[linear-gradient(118deg,rgba(45,212,191,0.16)_0%,transparent_42%),linear-gradient(318deg,rgba(252,211,77,0.12)_0%,transparent_36%)]" />
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
        leftColor="#14b8a6"
        rightColor="#fcd34d"
        baseDark="#08111f"
        animationSpeed={24}
        animationType="rotate-slide"
        wander
        wanderSpeed={92}
      />
    </div>
  );
}

function AngeeIdentity({
  tone = "default",
}: {
  tone?: "default" | "inverse";
}): ReactNode {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-3",
        tone === "inverse" ? "text-n-0" : "text-fg",
      )}
    >
      <span
        className={cn(
          "grid size-9 place-content-center rounded-md border",
          tone === "inverse"
            ? "border-n-0/15 bg-n-0/8"
            : "border-border-subtle bg-sheet",
        )}
      >
        <AngeeLogo
          aria-hidden="true"
          preset="gold"
          geometry="full"
          bgColor={null}
          size={22}
          width={22}
          height={22}
        />
      </span>
      <span className="text-xl font-semibold">Angee</span>
    </div>
  );
}

function DefaultCardHeader({
  brand,
}: {
  brand?: ReactNode;
}): ReactNode {
  const t = useBaseT();
  if (brand) {
    return (
      <div className="mb-8">
        <div className="mb-6">{brand}</div>
        <h1 className="text-28 font-semibold leading-tight text-fg">
          {t("auth.signIn")}
        </h1>
        <p className="mt-2 text-sm text-fg-muted">
          {t("auth.signInSubtextBranded")}
        </p>
      </div>
    );
  }

  return (
    <div className="mb-8 grid grid-cols-[4rem_minmax(0,1fr)] items-start gap-4 text-left">
      <div
        className="pointer-events-none grid size-16 p-2"
        aria-hidden="true"
      >
        <AngeeLogoCube
          size={9}
          gap={0.75}
          leftColor="#9A7D0A"
          rightColor="#E6B400"
          baseDark="#FCD34D"
          animationSpeed={18}
          animationType="rotate"
        />
      </div>
      <div className="min-w-0 pt-1">
        <p className="mb-2 text-base font-semibold leading-none text-fg">
          Angee
        </p>
        <h1 className="text-28 font-semibold leading-tight text-fg">
          {t("auth.signIn")}
        </h1>
        <p className="mt-2 text-sm text-fg-muted">
          {t("auth.signInSubtext")}
        </p>
      </div>
    </div>
  );
}

function DefaultPasswordHelp(): ReactNode {
  const t = useBaseT();
  return (
    <Button
      type="button"
      variant="link"
      size="sm"
      className="!h-auto px-0 py-0 text-sm font-medium"
    >
      {t("auth.forgotPassword")}
    </Button>
  );
}
