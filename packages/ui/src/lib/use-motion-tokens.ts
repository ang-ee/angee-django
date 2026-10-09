import * as React from "react";
import { useOptionalAppearance } from "../theme/appearance";

interface MotionTiming {
  readonly duration: number;
  readonly easing: string;
}

const FALLBACK_MOTION_TOKENS: MotionTiming = {
  duration: 180,
  easing: "cubic-bezier(0.2, 0.6, 0.2, 1)",
};

/** Read motion timing per mount and refresh it after the effective theme changes. */
export function useMotionTokens(): MotionTiming {
  const appearance = useOptionalAppearance();
  const effectiveThemeId = appearance?.effectiveThemeId;
  const followsAppearance = appearance !== null;
  const [tokens, setTokens] = React.useState(readMotionTokens);
  React.useEffect(() => {
    if (!followsAppearance) return;
    let active = true;
    queueMicrotask(() => {
      if (active) setTokens(readMotionTokens());
    });
    return () => { active = false; };
  }, [effectiveThemeId, followsAppearance]);
  return tokens;
}

function readMotionTokens(): MotionTiming {
  if (typeof document === "undefined" || typeof getComputedStyle === "undefined") {
    return FALLBACK_MOTION_TOKENS;
  }
  const styles = getComputedStyle(document.documentElement);
  return {
    duration: parseDuration(styles.getPropertyValue("--dur-base")),
    easing: styles.getPropertyValue("--ease").trim() || FALLBACK_MOTION_TOKENS.easing,
  };
}

function parseDuration(value: string): number {
  const match = /^(\d+(?:\.\d+)?)(ms|s)$/.exec(value.trim());
  if (!match) return FALLBACK_MOTION_TOKENS.duration;
  const duration = Number(match[1]);
  return match[2] === "s" ? duration * 1000 : duration;
}
