import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import { useIcon } from "./icon-registry";

export interface GlyphProps {
  name: string;
  /** Rendered when `name` resolves to no registered icon (e.g. `"help"`). */
  fallbackName?: string;
  size?: number | string;
  className?: string;
  decorative?: boolean;
  label?: string;
}

export function Glyph({
  name,
  fallbackName,
  size,
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
      style={sizeStyle}
    />
  );
}
