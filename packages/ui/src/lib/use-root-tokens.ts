import * as React from "react";
import { useStableArray } from "@angee/refine";
import { useOptionalAppearance } from "../theme/appearance";

interface MotionTiming {
  readonly duration: number;
  readonly easing: string;
}

const FALLBACK_MOTION_TOKENS: MotionTiming = {
  duration: 180,
  easing: "cubic-bezier(0.2, 0.6, 0.2, 1)",
};
const MOTION_TOKENS = ["--dur-base", "--ease"] as const;

/**
 * The resolved values of `names` on the document root, for code that cannot use `var()`:
 * read per mount, and read again when the names change or the appearance provider applies
 * another theme, color scheme or set of theme options. A name the root leaves unset reads
 * as "", as does every name without a document. The record keeps its identity while every
 * value stays the same.
 */
export function useRootTokens<Name extends string>(names: readonly Name[]): Readonly<Record<Name, string>> {
  const stableNames = useStableArray(names) as readonly Name[];
  const appearance = useOptionalAppearance();
  const themeId = appearance?.effectiveThemeId;
  const colorScheme = appearance?.colorScheme;
  const options = appearance?.effectiveOptions;
  const resolvingIdentity = appearance?.resolvingIdentity;
  const [tokens, setTokens] = React.useState(() => readRootTokens(stableNames));
  React.useEffect(() => {
    let active = true;
    // The provider applies the appearance in its own effect, which runs after this one.
    queueMicrotask(() => {
      if (!active) return;
      const next = readRootTokens(stableNames);
      setTokens((current) => sameTokens(current, next, stableNames) ? current : next);
    });
    return () => { active = false; };
  }, [stableNames, themeId, colorScheme, options, resolvingIdentity]);
  return tokens;
}

/**
 * Motion timing from the root's tokens: the stock duration when the root's does not parse,
 * and the root's easing as it is, or the stock one when the root has none.
 */
export function useMotionTokens(): MotionTiming {
  const tokens = useRootTokens(MOTION_TOKENS);
  return React.useMemo(() => ({
    duration: parseDuration(tokens["--dur-base"]),
    easing: tokens["--ease"] || FALLBACK_MOTION_TOKENS.easing,
  }), [tokens]);
}

function readRootTokens<Name extends string>(names: readonly Name[]): Readonly<Record<Name, string>> {
  const styles = typeof document === "undefined" || typeof getComputedStyle === "undefined"
    ? undefined
    : getComputedStyle(document.documentElement);
  return Object.fromEntries(names.map((name) => [name, styles?.getPropertyValue(name).trim() ?? ""])) as Record<Name, string>;
}

function sameTokens<Name extends string>(current: Readonly<Record<Name, string>>, next: Readonly<Record<Name, string>>, names: readonly Name[]): boolean {
  return Object.keys(current).length === names.length && names.every((name) => current[name] === next[name]);
}

function parseDuration(value: string): number {
  const match = /^(\d+(?:\.\d+)?)(ms|s)$/.exec(value);
  if (!match) return FALLBACK_MOTION_TOKENS.duration;
  const duration = Number(match[1]);
  return match[2] === "s" ? duration * 1000 : duration;
}
