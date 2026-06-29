import * as React from "react";

import { renderGlyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { toneClass, toneText } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

// The square icon chip that fronts a metric tile, mini card, or surface header.
// Its tone routes through the `tones.ts` owner (soft fill + tone-colored text)
// instead of hand-typing a soft pair, so it can't drift from the palette. The
// tile carries no border-width, so the soft fill's `border-*` color is inert.
const ICON_TILE_TONES = {
  neutral: cn(toneClass("neutral", "soft"), toneText("neutral")),
  brand: cn(toneClass("brand", "soft"), toneText("brand")),
} as const;

export const iconTileVariants = tv({
  base: "grid shrink-0 place-content-center rounded-6",
  variants: {
    size: {
      sm: "size-6 [&_.glyph]:size-3.5 [&>svg]:size-3.5",
      md: "size-7 [&_.glyph]:size-3.5 [&>svg]:size-3.5",
      lg: "size-8 [&_.glyph]:size-4 [&>svg]:size-4",
    },
    tone: ICON_TILE_TONES,
  },
  defaultVariants: {
    size: "md",
    tone: "neutral",
  },
});

export type IconTileRecipeProps = VariantProps<typeof iconTileVariants>;
export type IconTileSize = NonNullable<IconTileRecipeProps["size"]>;
export type IconTileTone = NonNullable<IconTileRecipeProps["tone"]>;

export type IconTileProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  IconTileRecipeProps & {
    className?: string;
    /** A registry glyph name or any node (composes the shared `renderGlyph`). */
    icon?: React.ReactNode | string;
  };

/** The square icon chip — composes `iconTileVariants` with the shared glyph
 *  renderer so every metric/header fragment reads the one chip recipe. */
export const IconTile = React.forwardRef<HTMLSpanElement, IconTileProps>(
  function IconTile({ className, icon, size = "md", tone = "neutral", ...props }, ref) {
    return (
      <span
        ref={ref}
        className={iconTileVariants({ className, size, tone })}
        {...props}
      >
        {renderGlyph(icon)}
      </span>
    );
  },
);
IconTile.displayName = "IconTile";
