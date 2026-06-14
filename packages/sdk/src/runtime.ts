import { useCallback, useMemo } from "react";

import type {
  ChatterContribution,
  ComposedMenuItem,
  FormOverrideMap,
  PreviewContribution,
  SlotContribution,
  WidgetMap,
} from "./define-addon";
import type { I18nResources, MessageResources, MessageVars } from "./i18n";
import { interpolateMessage, translateWithFallback } from "./i18n";
import { makeContext } from "./make-context";

/**
 * The merged app runtime an app composes once from its addon manifests. The
 * registry lookups (`useWidget` / `useMenus` / `useSlot` / `useT`) read from it;
 * there is no separate provider per registry.
 */
export interface AppRuntime {
  widgets: WidgetMap;
  menus: readonly ComposedMenuItem[];
  i18n: I18nResources;
  icons: Readonly<Record<string, unknown>>;
  forms: FormOverrideMap;
  chatter: readonly ChatterContribution[];
  slots: readonly SlotContribution[];
  previews: readonly PreviewContribution[];
}

const EMPTY_RUNTIME: AppRuntime = {
  widgets: {},
  menus: [],
  i18n: {},
  icons: {},
  forms: {},
  chatter: [],
  slots: [],
  previews: [],
};

const RuntimeContext = makeContext<AppRuntime>("AppRuntime");

/** Provide the runtime, filling any unset registry with its empty default. */
export function AppRuntimeProvider(props: {
  runtime: Partial<AppRuntime>;
  children: React.ReactNode;
}): React.ReactNode {
  const { runtime } = props;
  const value = useMemo<AppRuntime>(
    () => ({ ...EMPTY_RUNTIME, ...runtime }),
    [runtime],
  );
  return RuntimeContext.Provider({ value, children: props.children });
}

/** The merged runtime, or the empty runtime when unprovided. */
export function useAppRuntime(): AppRuntime {
  return RuntimeContext.useMaybe() ?? EMPTY_RUNTIME;
}

/** Look up a contributed widget by id. */
export function useWidget(id: string): unknown {
  return useAppRuntime().widgets[id];
}

/** Look up an addon-registered create-form override for a model (or undefined). */
export function useFormOverride(model: string): unknown {
  // `?.` guards a `Partial<AppRuntime>` provider that spread `forms: undefined`.
  return useAppRuntime().forms?.[model];
}

/** The merged menu list. */
export function useMenus(): readonly ComposedMenuItem[] {
  return useAppRuntime().menus;
}

/** The slot entries contributed to one slot, in merged order. */
export function useSlot(slot: string): readonly SlotContribution[] {
  const { slots } = useAppRuntime();
  return useMemo(() => slots.filter((entry) => entry.slot === slot), [slots, slot]);
}

/** The addon-contributed file-preview renderers, in composed order. */
export function usePreviews(): readonly PreviewContribution[] {
  return useAppRuntime().previews;
}

/** A translator bound to one namespace; resolves keys against merged i18n. */
export function useT(namespace: string): (key: string, vars?: MessageVars) => string {
  const { i18n } = useAppRuntime();
  return useMemo(() => {
    const messages = i18n[namespace] ?? {};
    return (key: string, vars: MessageVars = {}) =>
      interpolateMessage(messages[key] ?? key, vars);
  }, [i18n, namespace]);
}

/**
 * A namespaced translator with a bundled-English `fallback`: resolves a key
 * against the host runtime's merged i18n for `namespace`, then falls back to
 * `fallback`, then the key. The one owner of the translate-with-fallback pattern
 * — `@angee/base`'s `useBaseT` and each addon's `useXT` build on it — so a
 * component renders its English even before its runtime bundle is mounted
 * (unit tests, storybook, provider-less embeds). Stable identity (memoized on
 * the namespace translator) for use in dependency arrays.
 */
export function useNamespaceT(
  namespace: string,
  fallback: MessageResources,
): (key: string, vars?: MessageVars) => string {
  const t = useT(namespace);
  return useCallback(
    (key: string, vars: MessageVars = {}) =>
      translateWithFallback(t, fallback, key, vars),
    [t, fallback],
  );
}
