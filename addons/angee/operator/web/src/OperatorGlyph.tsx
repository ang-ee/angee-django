import type { SVGProps } from "react";

export type OperatorGlyphProps = SVGProps<SVGSVGElement> & {
  size?: number | string;
  strokeWidth?: number | string;
};

export function OperatorGlyph({
  size = 24,
  strokeWidth = 2,
  ...props
}: OperatorGlyphProps) {
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

/** A scroll/log-lines glyph for the operator logs drawer's edge stripe-tab. */
export function OperatorLogsGlyph({
  size = 24,
  strokeWidth = 2,
  ...props
}: OperatorGlyphProps) {
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
      <path d="M5 4.5h11.5a2.5 2.5 0 0 1 2.5 2.5v12.5a0 0 0 0 1 0 0H8a3 3 0 0 1-3-3z" />
      <path d="M8.5 8.5h7" />
      <path d="M8.5 12h7" />
      <path d="M8.5 15.5h4" />
    </svg>
  );
}
