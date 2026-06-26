import type { SVGProps } from "react";

export type AngeeMarkProps = SVGProps<SVGSVGElement> & {
  size?: number | string;
  /** Accepted for icon-component parity; the mark has no strokes. */
  strokeWidth?: number | string;
};

/** The Angee brand mark — the isometric three-tone cube cluster. */
export function AngeeMark({
  size = 20,
  strokeWidth: _strokeWidth,
  ...props
}: AngeeMarkProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="-200.92 -10 401.84 460.18"
      width={size}
      height={size}
      {...props}
    >
      <polygon points="0,147.45 63.64,183.95 0,220.45 -63.64,183.95" fill="#fcd34d" />
      <polygon points="63.64,183.95 63.64,257.67 0,294.18 0,220.45" fill="#e6b400" />
      <polygon points="-63.64,183.95 0,220.45 0,294.18 -63.64,257.67" fill="#9a7d0a" />
      <polygon points="-63.64,183.95 0,220.45 -63.64,256.95 -127.28,220.45" fill="#fcd34d" />
      <polygon points="0,220.45 0,294.18 -63.64,330.68 -63.64,256.95" fill="#e6b400" />
      <polygon points="-127.28,220.45 -63.64,256.95 -63.64,330.68 -127.28,294.18" fill="#9a7d0a" />
      <polygon points="0,0 63.64,36.5 0,73 -63.64,36.5" fill="#fcd34d" />
      <polygon points="63.64,36.5 63.64,110.23 0,146.73 0,73" fill="#e6b400" />
      <polygon points="-63.64,36.5 0,73 0,146.73 -63.64,110.23" fill="#9a7d0a" />
      <polygon points="127.28,220.45 190.92,256.95 127.28,293.46 63.64,256.95" fill="#fcd34d" />
      <polygon points="190.92,256.95 190.92,330.68 127.28,367.18 127.28,293.46" fill="#e6b400" />
      <polygon points="63.64,256.95 127.28,293.46 127.28,367.18 63.64,330.68" fill="#9a7d0a" />
      <polygon points="-127.28,220.45 -63.64,256.95 -127.28,293.46 -190.92,256.95" fill="#fcd34d" />
      <polygon points="-63.64,256.95 -63.64,330.68 -127.28,367.18 -127.28,293.46" fill="#e6b400" />
      <polygon points="-190.92,256.95 -127.28,293.46 -127.28,367.18 -190.92,330.68" fill="#9a7d0a" />
      <polygon points="63.64,36.5 127.28,73 63.64,109.51 0,73" fill="#fcd34d" />
      <polygon points="127.28,73 127.28,146.73 63.64,183.23 63.64,109.51" fill="#e6b400" />
      <polygon points="0,73 63.64,109.51 63.64,183.23 0,146.73" fill="#9a7d0a" />
      <polygon points="127.28,146.73 190.92,183.23 127.28,219.73 63.64,183.23" fill="#fcd34d" />
      <polygon points="190.92,183.23 190.92,256.95 127.28,293.46 127.28,219.73" fill="#e6b400" />
      <polygon points="63.64,183.23 127.28,219.73 127.28,293.46 63.64,256.95" fill="#9a7d0a" />
      <polygon points="-127.28,146.73 -63.64,183.23 -127.28,219.73 -190.92,183.23" fill="#fcd34d" />
      <polygon points="-63.64,183.23 -63.64,256.95 -127.28,293.46 -127.28,219.73" fill="#e6b400" />
      <polygon points="-190.92,183.23 -127.28,219.73 -127.28,293.46 -190.92,256.95" fill="#9a7d0a" />
      <polygon points="-63.64,256.95 0,293.46 -63.64,329.96 -127.28,293.46" fill="#fcd34d" />
      <polygon points="0,293.46 0,367.18 -63.64,403.68 -63.64,329.96" fill="#e6b400" />
      <polygon points="-127.28,293.46 -63.64,329.96 -63.64,403.68 -127.28,367.18" fill="#9a7d0a" />
      <polygon points="127.28,73 190.92,109.51 127.28,146.01 63.64,109.51" fill="#fcd34d" />
      <polygon points="190.92,109.51 190.92,183.23 127.28,219.73 127.28,146.01" fill="#e6b400" />
      <polygon points="63.64,109.51 127.28,146.01 127.28,219.73 63.64,183.23" fill="#9a7d0a" />
      <polygon points="-127.28,73 -63.64,109.51 -127.28,146.01 -190.92,109.51" fill="#fcd34d" />
      <polygon points="-63.64,109.51 -63.64,183.23 -127.28,219.73 -127.28,146.01" fill="#e6b400" />
      <polygon points="-190.92,109.51 -127.28,146.01 -127.28,219.73 -190.92,183.23" fill="#9a7d0a" />
      <polygon points="0,293.46 63.64,329.96 0,366.46 -63.64,329.96" fill="#fcd34d" />
      <polygon points="63.64,329.96 63.64,403.68 0,440.18 0,366.46" fill="#e6b400" />
      <polygon points="-63.64,329.96 0,366.46 0,440.18 -63.64,403.68" fill="#9a7d0a" />
      <polygon points="63.64,109.51 127.28,146.01 63.64,182.51 0,146.01" fill="#fcd34d" />
      <polygon points="127.28,146.01 127.28,219.73 63.64,256.23 63.64,182.51" fill="#e6b400" />
      <polygon points="0,146.01 63.64,182.51 63.64,256.23 0,219.73" fill="#9a7d0a" />
      <polygon points="0,146.01 63.64,182.51 0,219.01 -63.64,182.51" fill="#fcd34d" />
      <polygon points="63.64,182.51 63.64,256.23 0,292.74 0,219.01" fill="#e6b400" />
      <polygon points="-63.64,182.51 0,219.01 0,292.74 -63.64,256.23" fill="#9a7d0a" />
    </svg>
  );
}
