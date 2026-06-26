import {
  createContext,
  createElement,
  useContext,
  type Context,
  type ReactNode,
} from "react";

/**
 * A React context paired with its access hooks. `use()` requires a surrounding
 * provider and throws a named error otherwise; `useMaybe()` returns null when
 * unprovided, for optional consumers.
 */
export interface ContextBinding<T> {
  Provider: (props: { value: T; children: ReactNode }) => ReactNode;
  use: () => T;
  useMaybe: () => T | null;
}

// A private sentinel distinguishes "no provider" from a provided `null` value,
// so a context whose value is legitimately nullable still throws only when
// truly unprovided.
const ABSENT = Symbol("absent");

/**
 * Build a context and its access hooks in one call. This is the single home for
 * the provider/consumer boilerplate every runtime context would otherwise
 * repeat: declare the value type, name the context for error messages, and read
 * it with `use()` (required) or `useMaybe()` (optional).
 */
export function makeContext<T>(name: string): ContextBinding<T> {
  const Ctx = createContext<T | typeof ABSENT>(ABSENT) as Context<
    T | typeof ABSENT
  >;
  Ctx.displayName = name;

  function Provider({ value, children }: { value: T; children: ReactNode }) {
    return createElement(Ctx.Provider, { value }, children);
  }

  function use(): T {
    const value = useContext(Ctx);
    if (value === ABSENT) {
      throw new Error(`${name} is unavailable: render within its <${name}> provider.`);
    }
    return value;
  }

  function useMaybe(): T | null {
    const value = useContext(Ctx);
    return value === ABSENT ? null : value;
  }

  return { Provider, use, useMaybe };
}
