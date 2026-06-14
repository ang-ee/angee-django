import {
  Fragment,
  isValidElement,
  useCallback,
  type ReactElement,
  type ReactNode,
} from "react";
import { useNavigate } from "@tanstack/react-router";
import { AngeeLogo, AngeeLogoCube } from "@angee/logo-react";
import "@angee/logo-react/style.css";
import { useSlot, type SlotContribution } from "@angee/sdk";

import { cn } from "../lib/cn";
import { safeRedirectPath } from "./safe-redirect";
import { UsernamePasswordForm } from "./UsernamePasswordForm";
import { Button } from "../ui/button";

export const AUTH_LOGIN_METHOD_SLOT = "auth.login.method";
export const AUTH_LOGIN_CARD_FOOTER_SLOT = "auth.login.card-footer";
export const AUTH_LOGIN_PAGE_FOOTER_SLOT = "auth.login.page-footer";
export const AUTH_LOGIN_PASSWORD_HELP_SLOT = "auth.login.password-help";

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
        showHero
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
          className="relative z-10 hidden min-h-screen lg:block"
          aria-hidden={defaultHero ? "true" : undefined}
        >
          {resolvedHero}
        </div>
      ) : null}
      <section
        className={cn(
          "relative z-10 flex min-h-screen items-center justify-center px-5 py-8 sm:px-8",
          showHero
            ? "border-l border-n-0/20 bg-sheet/94 shadow-2xl shadow-n-950/20 backdrop-blur-xl"
            : "bg-sheet",
          !showHero && "min-h-0 w-full max-w-[30rem] rounded-lg border border-border shadow-lg",
        )}
      >
        <div className="w-full max-w-[26rem]">
          <div className={cn("mb-9 flex justify-center", showHero && "lg:hidden")}>
            {brand ?? <AngeeIdentity />}
          </div>
          {cardHeader === null ? null : (
            <div className="mb-8">{cardHeader ?? <DefaultCardHeader />}</div>
          )}
          {loginMethods ? (
            <div className="mb-5">
              {loginMethods}
              <div className="mt-5 flex items-center gap-3 text-xs text-fg-muted">
                <span className="h-px flex-1 bg-border-subtle" aria-hidden />
                <span>or use password</span>
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
  return (
    <section className="relative flex min-h-screen flex-col overflow-hidden px-10 py-9 text-n-0 xl:px-12">
      <div className="relative z-10">{brand ?? <AngeeIdentity tone="inverse" />}</div>
      <div className="relative z-10 flex flex-1 items-center pb-16 pt-10">
        <div className="w-full max-w-xl">
          <p className="mb-5 text-xs font-semibold uppercase tracking-[0.18em] text-n-0/72">
            From intent to interface
          </p>
          <h1 className="max-w-[11ch] text-5xl font-semibold leading-[1.02] text-n-0 xl:text-6xl">
            Build what you can imagine.
          </h1>
          <p className="mt-5 max-w-md text-base leading-7 text-n-100/88">
            Shape the idea. Watch it become a living product surface.
          </p>
        </div>
      </div>
    </section>
  );
}

function LoginVisualBackdrop({
  imageUrl,
  showAtmosphere,
}: {
  imageUrl?: string;
  showAtmosphere: boolean;
}): ReactNode {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      {imageUrl ? (
        <div
          className="absolute inset-0 bg-cover bg-center bg-no-repeat"
          style={{ backgroundImage: `url("${imageUrl}")` }}
        />
      ) : (
        <div className="absolute inset-0 bg-[linear-gradient(135deg,#07111f,#123129_46%,#08101d)]" />
      )}
      <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(4,9,18,0.82)_0%,rgba(4,9,18,0.34)_44%,rgba(4,9,18,0.9)_100%)]" />
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(4,9,18,0.08)_0%,rgba(4,9,18,0.16)_48%,rgba(4,9,18,0.68)_100%)]" />
      {showAtmosphere ? (
        <>
          <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(255,255,255,0.09)_1px,transparent_1px),linear-gradient(rgba(255,255,255,0.07)_1px,transparent_1px)] bg-[size:56px_56px] opacity-30 [mask-image:linear-gradient(90deg,black,transparent_72%)]" />
          <div className="absolute inset-0 bg-[linear-gradient(118deg,rgba(45,212,191,0.2)_0%,transparent_42%),linear-gradient(318deg,rgba(252,211,77,0.16)_0%,transparent_36%)]" />
          <div className="absolute inset-x-0 top-0 h-24 border-b border-n-0/10 bg-n-950/10 backdrop-blur-[1px]" />
        </>
      ) : null}
    </div>
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

function DefaultCardHeader(): ReactNode {
  return (
    <div>
      <h1 className="text-28 font-semibold leading-tight text-fg">
        Sign in
      </h1>
      <p className="mt-2 text-sm text-fg-muted">
        Use your Angee account credentials.
      </p>
    </div>
  );
}

function DefaultPasswordHelp(): ReactNode {
  return (
    <Button
      type="button"
      variant="link"
      size="sm"
      className="!h-auto px-0 py-0 text-sm font-medium"
    >
      Forgot your password?
    </Button>
  );
}

function SlotOutlet({
  entries,
}: {
  entries: readonly SlotContribution[];
}): ReactElement | null {
  const nodes = entries.flatMap((entry) => slotNode(entry.content, entry.id));
  return nodes.length > 0 ? <>{nodes}</> : null;
}

function slotNode(value: unknown, key: string): ReactNode[] {
  if (value == null || typeof value === "boolean") return [];
  if (typeof value === "string" || typeof value === "number") {
    return [<span key={key}>{value}</span>];
  }
  if (isValidElement(value)) return [<Fragment key={key}>{value}</Fragment>];
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => slotNode(item, `${key}:${index}`));
  }
  return [];
}

function slotEntriesHaveContent(entries: readonly SlotContribution[]): boolean {
  return entries.some((entry) => slotNode(entry.content, entry.id).length > 0);
}
