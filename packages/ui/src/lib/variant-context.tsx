import * as React from "react";

export interface VariantContext<T> {
  Provider: React.Provider<T>;
  /** The explicit `variant` if given, else the nearest provided one (else default). */
  useVariant: (variant?: T) => T;
}

/**
 * A tiny variant context: a compound component sets its `variant` once at the
 * root and parts inherit it unless they override. One owner for the
 * `createContext + (variant ?? useContext)` shape tabs/accordion/collapsible
 * each re-spelled.
 */
export function createVariantContext<T>(defaultValue: T): VariantContext<T> {
  const Context = React.createContext<T>(defaultValue);
  function useVariant(variant?: T): T {
    const inherited = React.useContext(Context);
    return variant ?? inherited;
  }
  return { Provider: Context.Provider, useVariant };
}
