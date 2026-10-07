/** A `#rrggbb` colour, the only form the colour widget and backend `ColorField` exchange. */
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/** The value as a `#rrggbb` colour, or `undefined` for anything else (blank means no colour). */
export function hexColor(value: unknown): string | undefined {
  return typeof value === "string" && HEX_COLOR.test(value) ? value : undefined;
}

/** Dark or light ink, whichever reads on a `#rrggbb` fill (WCAG relative luminance). */
export function readableInk(hex: string): string {
  const channel = (offset: number) => {
    const value = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
  return luminance > 0.36 ? "rgb(17 24 39)" : "rgb(255 255 255)";
}
