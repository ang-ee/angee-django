import * as React from "react";

interface MotionTiming {
  readonly duration: number;
  readonly easing: string;
}

const FALLBACK_MOTION_TOKENS: MotionTiming = {
  duration: 180,
  easing: "cubic-bezier(0.2, 0.6, 0.2, 1)",
};

let cachedMotionTokens: MotionTiming | undefined;

/** Read the base motion timing once, with the intrinsic theme tokens as the SSR fallback. */
export function useMotionTokens(): MotionTiming {
  const [tokens] = React.useState(readMotionTokens);
  return tokens;
}

function readMotionTokens(): MotionTiming {
  if (typeof document === "undefined" || typeof getComputedStyle === "undefined") {
    return FALLBACK_MOTION_TOKENS;
  }
  if (cachedMotionTokens) return cachedMotionTokens;

  const styles = getComputedStyle(document.documentElement);
  cachedMotionTokens = {
    duration: parseDuration(styles.getPropertyValue("--dur-base")),
    easing: styles.getPropertyValue("--ease").trim() || FALLBACK_MOTION_TOKENS.easing,
  };
  return cachedMotionTokens;
}

function parseDuration(value: string): number {
  const match = /^(\d+(?:\.\d+)?)(ms|s)$/.exec(value.trim());
  if (!match) return FALLBACK_MOTION_TOKENS.duration;
  const duration = Number(match[1]);
  return match[2] === "s" ? duration * 1000 : duration;
}
