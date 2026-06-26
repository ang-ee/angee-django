import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

export const brandLockupVariants = tv({
  slots: {
    root: "flex items-center gap-3",
    mark: "grid shrink-0 place-content-center [&_.glyph]:size-full [&>svg]:size-full",
    label: "font-extrabold leading-tight",
  },
  variants: {
    size: {
      sm: {
        mark: "size-7",
        label: "text-xl",
      },
      md: {
        mark: "size-9",
        label: "text-3xl md:text-4xl",
      },
      lg: {
        mark: "size-11",
        label: "text-4xl md:text-5xl",
      },
    },
    tone: {
      default: {
        label: "text-fg",
      },
      inverse: {
        label: "text-white drop-shadow-md",
      },
    },
  },
  defaultVariants: {
    size: "md",
    tone: "default",
  },
});

type BrandLockupRecipeProps = VariantProps<typeof brandLockupVariants>;

export type BrandLockupSize = NonNullable<BrandLockupRecipeProps["size"]>;
export type BrandLockupTone = NonNullable<BrandLockupRecipeProps["tone"]>;

export interface BrandLockupProps {
  label: React.ReactNode;
  mark?: React.ReactNode;
  size?: BrandLockupSize;
  tone?: BrandLockupTone;
  className?: string;
}

export function BrandLockup({
  label,
  mark,
  size = "md",
  tone = "default",
  className,
}: BrandLockupProps): React.ReactElement {
  const styles = brandLockupVariants({ size, tone });

  return (
    <div className={styles.root({ className })}>
      {mark ? <span className={styles.mark()}>{mark}</span> : null}
      <span className={styles.label()}>{label}</span>
    </div>
  );
}

export interface AnnouncementChipProps {
  children: React.ReactNode;
  detail?: React.ReactNode;
  pulse?: boolean;
  tone?: "default" | "inverse";
  className?: string;
}

export function AnnouncementChip({
  children,
  detail,
  pulse = false,
  tone = "default",
  className,
}: AnnouncementChipProps): React.ReactElement {
  return (
    <div
      className={cn(
        "mt-2 flex items-center gap-2 text-sm",
        tone === "inverse" ? "text-white/78" : "text-fg-2",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn("size-1.5 rounded-full bg-brand-300", pulse && "animate-pulse")}
      />
      <span
        className={cn(
          "font-mono text-xs uppercase text-brand-300",
          tone === "inverse" && "drop-shadow-sm",
        )}
      >
        {children}
      </span>
      {detail ? (
        <>
          <span aria-hidden="true">/</span>
          <span>{detail}</span>
        </>
      ) : null}
    </div>
  );
}

export interface MarketingHeroProps {
  brand?: React.ReactNode;
  preview?: React.ReactNode;
  headline: React.ReactNode;
  body?: React.ReactNode;
  actions?: React.ReactNode;
  commandStrip?: React.ReactNode | null;
  className?: string;
  contentClassName?: string;
}

export function MarketingHero({
  brand,
  preview,
  headline,
  body,
  actions,
  commandStrip = null,
  className,
  contentClassName,
}: MarketingHeroProps): React.ReactElement {
  return (
    <section
      className={cn(
        "relative hidden min-h-0 flex-col justify-between gap-8 px-12 py-16 lg:flex",
        className,
      )}
    >
      {brand || preview ? (
        <div>
          {brand}
          {preview}
        </div>
      ) : null}
      <div className={cn("flex flex-1 flex-col justify-center", contentClassName)}>
        <div className="flex flex-col gap-6">
          <h1 className="max-w-2xl text-5xl font-extrabold leading-[1.1] text-white drop-shadow-md xl:text-6xl">
            {headline}
          </h1>
          {body ? (
            <div className="max-w-xl text-lg leading-relaxed text-white/82">
              {body}
            </div>
          ) : null}
          {actions ? (
            <div className="flex flex-wrap items-center gap-2">{actions}</div>
          ) : null}
        </div>
      </div>
      {commandStrip ? <div>{commandStrip}</div> : null}
    </section>
  );
}
