import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { toneText } from "../lib/tones";
import { barVariants } from "./bar";
import { createLayoutBand } from "./layout-band";

const band = createLayoutBand(
  "footer",
  // `px-3.5` is off the bar pad scale (the statusline's intentional snug edge),
  // so it rides on top of the bar recipe rather than through a `pad` variant.
  cn(
    barVariants({
      height: "status",
      edge: "top",
      tone: "sheet",
      gap: 4,
      text: "2xs-muted",
    }),
    "px-3.5",
  ),
);

export interface StatuslineProviderProps {
  children: React.ReactNode;
  host: HTMLElement | null | undefined;
}

/** Provide the statusline host (`area-status`) for the bar rendered below. */
export const StatuslineProvider: (
  props: StatuslineProviderProps,
) => React.ReactElement = band.Provider;

export interface StatuslineProps {
  /** Segments shown left of the spacer; pair with `<StatuslineSpacer />`. */
  children: React.ReactNode;
  className?: string;
}

/**
 * The page's flush statusline in the layout's `area-status` row — a low-profile
 * bottom bar of `StatusSegment`s (save state, counts, sync, cursor, …). It is
 * opt-in: a page renders `<Statusline>` to fill the row, which otherwise stays
 * collapsed. Under a `ConsoleLayout` it portals into `area-status`; standalone
 * it renders inline. Use `<StatuslineSpacer />` to push trailing segments right.
 */
export const Statusline: (
  props: StatuslineProps,
) => React.ReactElement | React.ReactPortal | null = band.Band;

export interface StatusSegmentProps {
  /** Icon registry name shown before the label. */
  icon?: string;
  /** Semantic tone (e.g. a saved/synced affordance reads as success). */
  tone?: "default" | "success" | "danger";
  children: React.ReactNode;
  className?: string;
  onClick?: () => void;
}

/** One statusline segment: an optional glyph plus a short label. */
export function StatusSegment({
  icon,
  tone = "default",
  children,
  className,
  onClick,
}: StatusSegmentProps): React.ReactElement {
  const content = (
    <>
      {icon ? <Glyph name={icon} className="size-3.5" /> : null}
      {children}
    </>
  );
  const tones = {
    default: "text-fg-muted",
    success: toneText("success"),
    danger: toneText("danger"),
  } as const;
  const base = cn("flex items-center gap-1.5", tones[tone], className);
  return onClick ? (
    <button type="button" className={cn(base, "hover:text-fg")} onClick={onClick}>
      {content}
    </button>
  ) : (
    <span className={base}>{content}</span>
  );
}

/** Push the segments after it to the right edge of the statusline. */
export function StatuslineSpacer(): React.ReactElement {
  return <span className="flex-1" />;
}
