import { useMemo } from "react";

import type {
  ChatterContribution,
  ComposedMenuItem,
  SlotContribution,
  WidgetMap,
} from "./define-addon";
import type { I18nResources, MessageVars } from "./i18n";
import { interpolateMessage } from "./i18n";
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
  chatter: readonly ChatterContribution[];
  slots: readonly SlotContribution[];
}

const EMPTY_RUNTIME: AppRuntime = {
  widgets: {},
  menus: [],
  i18n: {},
  icons: {},
  chatter: [],
  slots: [],
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

/** The merged menu list. */
export function useMenus(): readonly ComposedMenuItem[] {
  return useAppRuntime().menus;
}

/** The slot entries contributed to one slot, in merged order. */
export function useSlot(slot: string): readonly SlotContribution[] {
  const { slots } = useAppRuntime();
  return useMemo(() => slots.filter((entry) => entry.slot === slot), [slots, slot]);
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
