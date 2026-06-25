import type { ReactElement, ReactNode } from "react";

import { cn } from "../lib/cn";
import { useIcon } from "./icon-registry";

export interface GlyphProps {
  name: string;
  /** Rendered when `name` resolves to no registered icon (e.g. `"help"`). */
  fallbackName?: string;
  size?: number | string;
  /** Stroke weight passthrough — lift over lucide's default 2 where a glyph
   * reads thin at small sizes (a checkmark at 12px). */
  strokeWidth?: number | string;
  className?: string;
  decorative?: boolean;
  label?: string;
}

export function Glyph({
  name,
  fallbackName,
  size,
  strokeWidth,
  className,
  decorative = true,
  label,
}: GlyphProps): ReactElement | null {
  const primary = useIcon(name);
  const fallback = useIcon(fallbackName ?? "");
  const Icon = primary ?? fallback;
  if (!Icon) return null;
  const accessibleLabel = decorative ? undefined : (label ?? name);
  const sizeStyle = size === undefined ? undefined : { width: size, height: size };
  return (
    <Icon
      aria-hidden={decorative || undefined}
      aria-label={accessibleLabel}
      className={cn("glyph", className)}
      focusable="false"
      role={decorative ? undefined : "img"}
      size={size}
      strokeWidth={strokeWidth}
      style={sizeStyle}
    />
  );
}

/**
 * Render an icon "slot" value: a registry name becomes a decorative `<Glyph>`,
 * any other node passes through. The one owner for the
 * `typeof icon === "string" ? <Glyph name={icon}/> : icon` adapter that the
 * fragments each re-inlined.
 */
export function renderGlyph(icon: ReactNode): ReactNode {
  return typeof icon === "string" ? <Glyph decorative name={icon} /> : icon;
}
