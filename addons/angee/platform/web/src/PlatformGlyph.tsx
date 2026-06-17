import type { SVGProps } from "react";

export type PlatformGlyphProps = SVGProps<SVGSVGElement> & {
  size?: number | string;
  strokeWidth?: number | string;
};

/** The `>_` terminal-prompt mark — the platform/admin console glyph. */
export function PlatformGlyph({
  size = 24,
  strokeWidth = 2,
  ...props
}: PlatformGlyphProps) {
  return (
    <svg
      fill="none"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
      viewBox="0 0 24 24"
      width={size}
      {...props}
    >
      <path d="m6.5 7.25 5.25 4.75-5.25 4.75" />
      <path d="M13.75 16.75h4.75" />
    </svg>
  );
}
