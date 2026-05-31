import { useCallback, type ReactNode } from "react";
import { AngeeLogo } from "@angee/logo-react";
import { useNavigate } from "@tanstack/react-router";

import { PublicShell } from "../shell/PublicShell";
import { UsernamePasswordForm } from "./UsernamePasswordForm";

export interface LoginPageProps {
  brand?: ReactNode;
  redirectTo?: string;
  footer?: ReactNode;
  hero?: ReactNode | null;
  cardHeader?: ReactNode | null;
  showAtmosphere?: boolean;
  backgroundImageUrl?: string;
}

export function LoginPage({
  brand,
  redirectTo = "/",
  footer,
  hero,
  cardHeader,
  showAtmosphere,
  backgroundImageUrl,
}: LoginPageProps): ReactNode {
  const navigate = useNavigate();
  const onSuccess = useCallback(() => {
    const next =
      typeof window === "undefined"
        ? null
        : new URLSearchParams(window.location.search).get("next");
    const target = safeRedirect(next) ?? safeRedirect(redirectTo) ?? "/";
    if (typeof window === "undefined") {
      void navigate({ to: target });
      return;
    }
    window.location.assign(target);
  }, [navigate, redirectTo]);

  return (
    <PublicShell
      hero={hero === undefined ? <LoginHero /> : hero}
      cardLead={<MobileBrandLead brand={brand} />}
      footer={footer}
      showAtmosphere={showAtmosphere}
      backgroundImageUrl={backgroundImageUrl}
    >
      {cardHeader === null ? null : (cardHeader ?? <DefaultCardHeader />)}
      {cardHeader === null ? null : (
        <div className="mb-7 mt-7 h-px bg-border" aria-hidden="true" />
      )}
      <UsernamePasswordForm onSuccess={onSuccess} />
    </PublicShell>
  );
}

function LoginHero(): ReactNode {
  return (
    <section className="hidden min-h-screen flex-col justify-between px-8 py-10 text-n-0 lg:flex xl:px-12">
      <div>
        <BrandLockup />
      </div>
      <div className="max-w-2xl pb-16">
        <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-n-0/20 bg-n-0/10 px-3 py-1 text-xs font-semibold uppercase text-n-0 shadow-lg shadow-n-950/20 backdrop-blur">
          <span className="size-1.5 rounded-full bg-amber-300" aria-hidden />
          Alpha
          <span className="font-normal normal-case text-n-200">
            Agent-native generative execution environment
          </span>
        </div>
        <h1 className="max-w-[12ch] text-5xl font-semibold leading-[1.02] text-n-0 xl:text-6xl">
          Define your vision.
          <br />
          <span className="bg-gradient-to-br from-brand-200 via-brand-300 to-brand-500 bg-clip-text text-transparent">
            Agents build the reality.
          </span>
        </h1>
        <p className="mt-6 max-w-xl text-base leading-7 text-n-200">
          Compose Django, React, permissions, data views, and agent workflows
          into one deterministic product surface.
        </p>
      </div>
    </section>
  );
}

function MobileBrandLead({ brand }: { brand?: ReactNode }): ReactNode {
  return (
    <div className="lg:hidden">
      <div className="mb-4 flex justify-center">{brand ?? <BrandLockup />}</div>
      <p className="text-sm text-n-200">Sign in to continue.</p>
    </div>
  );
}

function BrandLockup(): ReactNode {
  return (
    <div className="inline-flex items-center gap-3 text-n-0">
      <AngeeLogo
        preset="gold"
        geometry="cube"
        bgColor={null}
        size={36}
        width={36}
        height={36}
      />
      <span className="text-sm font-semibold tracking-wide">angee</span>
    </div>
  );
}

function DefaultCardHeader(): ReactNode {
  return (
    <div>
      <h2 className="text-22 font-semibold text-fg">Welcome back</h2>
      <p className="mt-1.5 text-sm text-fg-muted">Sign in to your account.</p>
    </div>
  );
}

function safeRedirect(value: string | null | undefined): string | null {
  if (!value) return null;
  if (!value.startsWith("/") || value.startsWith("//")) return null;
  return value;
}
