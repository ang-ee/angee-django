import { lazy, type ComponentType, type ReactElement } from "react";

import { LazyBoundary } from "../fragments/LazyBoundary";
import { Skeleton } from "../ui/skeleton";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

type WidgetSlot = "read" | "edit" | "cell";

/**
 * A `WidgetDefinition` whose heavy implementation (CodeMirror, react-markdown,
 * react-json-view-lite) is code-split out of the boot bundle. Each slot is a
 * STABLE plain function component, built once at module init, so render sites and
 * `isWidgetDefinition` see an ordinary widget; on first render it lazy-loads the
 * real widget module and shows a skeleton meanwhile, composing the shared
 * {@link LazyBoundary}. `read` is always exposed; `edit`/`cell` only when asked
 * for (the registry reads `edit` to decide a field is editable).
 */
export function lazyWidget<TValue = unknown, TRow = unknown>(
  load: () => Promise<WidgetDefinition<TValue, TRow>>,
  slots: { edit?: boolean; cell?: boolean } = {},
): WidgetDefinition<TValue, TRow> {
  // Resolve the module once and share it across the slots' lazy factories — the
  // dynamic import is cached, so this just avoids re-wrapping it per slot.
  let pending: Promise<WidgetDefinition<TValue, TRow>> | undefined;
  const loadOnce = (): Promise<WidgetDefinition<TValue, TRow>> =>
    (pending ??= load());
  const definition: WidgetDefinition<TValue, TRow> = {
    read: lazySlot(loadOnce, "read"),
  };
  if (slots.edit) definition.edit = lazySlot(loadOnce, "edit");
  if (slots.cell) definition.cell = lazySlot(loadOnce, "cell");
  return definition;
}

function lazySlot<TValue, TRow>(
  loadOnce: () => Promise<WidgetDefinition<TValue, TRow>>,
  slot: WidgetSlot,
): ComponentType<WidgetRenderProps<TValue, TRow>> {
  const Lazy = lazy(async () => {
    const widget = await loadOnce();
    // Fall back to `read` for a slot the widget does not define (e.g. a
    // preview-only widget whose `edit` is just its read renderer).
    return { default: widget[slot] ?? widget.read };
  });
  return function LazyWidgetSlot(
    props: WidgetRenderProps<TValue, TRow>,
  ): ReactElement {
    return (
      <LazyBoundary pending={<Skeleton className="h-9 w-full" />}>
        <Lazy {...props} />
      </LazyBoundary>
    );
  };
}
