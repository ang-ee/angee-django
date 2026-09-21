import * as React from "react";

import type {
  MutationDialogProps,
  MutationDialogValues,
} from "./views/form/MutationDialog";

type UiModule = typeof import("./index");
export type UiTestDoubles = Partial<Record<keyof UiModule, unknown>>;

export interface UiRouteTestDoubleOptions {
  routeHref?: (route: string, parameters?: Record<string, unknown>) => string;
  recordHref?: (model: string, id: string) => string | undefined;
  search?: Readonly<Record<string, unknown>>;
  mediaQuery?: boolean;
}

/** Merge grouped UI doubles over the real module without repeating mock boilerplate. */
export async function createUiTestModule(
  importOriginal: <T = UiModule>() => Promise<T>,
  ...groups: readonly UiTestDoubles[]
): Promise<UiModule> {
  const original = await importOriginal<UiModule>();
  return Object.assign({}, original, ...groups) as UiModule;
}

/** Group the route/runtime hooks most addon view tests replace together. */
export function createUiRouteTestDoubles({
  routeHref = (route, parameters) => parameters?.id ? `/${route}/${String(parameters.id)}` : `/${route}`,
  recordHref = (model, id) => `/records/${model}/${id}`,
  search = {},
  mediaQuery = false,
}: UiRouteTestDoubleOptions = {}): UiTestDoubles {
  return {
    useRouteHref: () => routeHref,
    useResourceRecordHrefLookup: () => recordHref,
    useRouteSearch: () => search,
    useMediaQuery: () => mediaQuery,
  } as UiTestDoubles;
}

export type MutationDialogTestDoubleProps = MutationDialogProps<
  Record<string, unknown>,
  unknown
> &
  Record<string, unknown>;

export interface MutationDialogTestDoubleOptions {
  capture?: (props: MutationDialogTestDoubleProps) => void;
  values?:
    | MutationDialogValues
    | ((props: MutationDialogTestDoubleProps) => MutationDialogValues);
  submitLabel?:
    | React.ReactNode
    | ((props: MutationDialogTestDoubleProps) => React.ReactNode);
}

/**
 * Minimal shared test rendering for consumers that own MutationDialog
 * declarations rather than its interaction ceremony. Callers spread the real
 * `@angee/ui` module in their mock, so codecs and every unrelated UI owner stay
 * production-accurate.
 */
export function createMutationDialogTestDouble({
  capture,
  values = {},
  submitLabel = "Submit mutation dialog",
}: MutationDialogTestDoubleOptions = {}): (
  props: MutationDialogTestDoubleProps,
) => React.ReactElement | null {
  return function MutationDialogTestDouble(
    props: MutationDialogTestDoubleProps,
  ): React.ReactElement | null {
    const [open, setOpen] = React.useState(false);
    capture?.(props);
    const visible = props.open ?? open;
    const trigger = props.trigger
      ? React.cloneElement(
          props.trigger as React.ReactElement<{
            onClick?: React.MouseEventHandler<HTMLElement>;
          }>,
          { onClick: () => setOpen(true) },
        )
      : null;
    if (!visible) return trigger;

    const buttonLabel =
      typeof submitLabel === "function" ? submitLabel(props) : submitLabel;

    return <>{trigger}
      <form
        aria-label={String(props.title)}
        onSubmit={(event) => {
          event.preventDefault();
          const rawValues =
            typeof values === "function" ? values(props) : values;
          const parsed = props.parseValues({
            ...props.initialValues,
            ...rawValues,
          });
          void Promise.resolve(props.onSubmit(parsed)).then((result) => {
            props.onSubmitted?.(result, parsed);
          });
        }}
      >
        <button type="submit">{buttonLabel}</button>
      </form>
    </>;
  };
}
