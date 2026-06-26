import type { ReactNode } from "react";
import { AngeeLogo } from "@angee/logo-react";

import { cn } from "../lib/cn";

export interface PublicLayoutProps {
  children: ReactNode;
  hero?: ReactNode | null;
  cardLead?: ReactNode;
  brand?: ReactNode;
  footer?: ReactNode;
  showAtmosphere?: boolean;
  backgroundImageUrl?: string;
  className?: string;
}

export function PublicLayout({
  children,
  hero,
  cardLead,
  brand,
  footer,
  showAtmosphere = true,
  backgroundImageUrl,
  className,
}: PublicLayoutProps): ReactNode {
  const resolvedLead = cardLead ?? brand;
  const showHero = hero !== null && hero !== undefined;
  return (
    <div
      className={cn(
        "relative min-h-screen w-full overflow-hidden bg-n-950 text-fg",
        className,
      )}
    >
      {showAtmosphere ? (
        <BackgroundAtmosphere imageUrl={backgroundImageUrl} />
      ) : null}
      <div className="relative z-10 min-h-screen w-full">
        <div
          className={cn(
            "mx-auto grid min-h-screen w-full max-w-[1400px] items-stretch",
            showHero
              ? "lg:grid-cols-[minmax(0,1fr)_minmax(28rem,32rem)]"
              : "place-items-center px-4 py-10",
          )}
        >
          {showHero ? <div className="min-h-0">{hero}</div> : null}
          <section className="relative flex min-h-screen items-center justify-center px-4 py-10 sm:px-6 lg:justify-start lg:px-0">
            <div className="w-full max-w-md">
              {resolvedLead ? (
                <div className="mb-7 flex flex-col items-center gap-2 text-center">
                  {resolvedLead}
                </div>
              ) : null}
              <div className="rounded-lg border border-n-0/30 bg-n-0/94 p-6 shadow-2xl shadow-n-950/30 backdrop-blur-sm sm:p-8">
                {children}
              </div>
            </div>
          </section>
          {footer ? (
            <div className="col-span-full px-4 pb-8 text-center text-xs text-n-200 sm:px-6">
              {footer}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function BackgroundAtmosphere({
  imageUrl,
}: {
  imageUrl?: string;
}): ReactNode {
  return (
    <div
      className="pointer-events-none absolute inset-0 overflow-hidden"
      aria-hidden="true"
    >
      {imageUrl ? (
        <div
          className="absolute inset-0 bg-cover bg-center bg-no-repeat lg:bg-cover-wide"
          style={{ backgroundImage: `url("${imageUrl}")` }}
        />
      ) : (
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_16%,color-mix(in_oklab,var(--brand)_34%,transparent),transparent_34%),radial-gradient(circle_at_76%_20%,color-mix(in_oklab,var(--accent)_24%,transparent),transparent_30%),linear-gradient(135deg,var(--n-950),var(--n-900)_48%,var(--n-950))]" />
      )}
      <div className="absolute inset-0 bg-gradient-to-r from-n-950/70 via-n-950/20 to-transparent" />
      <div className="absolute inset-0 bg-gradient-to-b from-n-950/10 via-transparent to-n-950/30" />
      <div className="absolute inset-0 bg-grid opacity-15 mix-blend-screen" />
      <div className="absolute inset-x-0 top-0 h-24 border-b border-n-0/10 bg-n-950/10 backdrop-blur-[1px]" />
      <div className="absolute left-12 top-10 hidden opacity-45 xl:block">
        <AngeeLogo
          preset="gold"
          geometry="cube"
          bgColor={null}
          size={56}
          width={56}
          height={56}
        />
      </div>
    </div>
  );
}
