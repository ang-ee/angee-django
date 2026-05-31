import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import { getIcon } from "./icon-registry";

export interface GlyphProps {
  name: string;
  size?: number | string;
  className?: string;
  decorative?: boolean;
  label?: string;
}

export function Glyph({
  name,
  size = 16,
  className,
  decorative = true,
  label,
}: GlyphProps): ReactElement | null {
  const Icon = getIcon(name);
  if (!Icon) return null;
  const accessibleLabel = decorative ? undefined : (label ?? name);
  return (
    <Icon
      aria-hidden={decorative || undefined}
      aria-label={accessibleLabel}
      className={cn("glyph", className)}
      focusable="false"
      role={decorative ? undefined : "img"}
      size={size}
    />
  );
}
