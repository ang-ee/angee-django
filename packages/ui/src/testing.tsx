import * as React from "react";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRouter,
  type AnyRouter,
} from "@tanstack/react-router";
import {
  createRefineTestProviders,
  type RefineTestDataProvider,
  type RefineTestProviderOptions,
} from "@angee/refine/testing";
import {
  ModelMetadataProvider,
  refineResourcesFromDataResources,
  schemaFieldMetadataFromDataResources,
  type DataResourceMetadata,
  type SchemaFieldMetadata,
} from "@angee/metadata";

import { ModalsHost } from "./feedback/ModalsHost";
import { AppRuntimeProvider } from "./runtime/runtime";
import { InAppLinkProvider, type InAppNavigator } from "./lib/in-app-link";
import { TooltipProvider } from "./ui/tooltip";

import type {
  MutationDialogProps,
  MutationDialogValues,
} from "./views/form/MutationDialog";
import { submitMutationDialog } from "./views/form/MutationDialog";
import { SURFACE_RENDERS_FAILURE } from "./views/form/validation-errors";

type UiModule = typeof import("./index");
export type UiTestDoubles = Partial<Record<keyof UiModule, unknown>>;

export interface UiTestProviderOptions<T extends RefineTestDataProvider = RefineTestDataProvider>
  extends Omit<RefineTestProviderOptions<T>, "resources"> {
  navigate?: InAppNavigator;
  metadata?: SchemaFieldMetadata;
  resources?: readonly DataResourceMetadata[];
  /** Override the projected registry when a case exercises explicit routes or no registration. */
  refineResources?: RefineTestProviderOptions["resources"];
  /**
   * Render under a memory router at these entries (`true`: `/`), as an app page is: a form
   * dialog guards route moves while it holds work. Each mount's router is kept in `routers`.
   */
  router?: true | { initialEntries: readonly string[] };
}

/**
 * Add resource projection and metadata context to the native Refine test harness.
 * Tooltips open as soon as the pointer enters their trigger, so a hover assertion
 * never races Base UI's wall-clock open delay; a nested `TooltipProvider` keeps
 * its own delay. `ModalsHost` answers the confirmations a surface asks, as the app's
 * does; an `AppRuntimeProvider` holds the runtime's idempotency keys, fresh per mount,
 * under any the test renders; and `router` renders under a memory router whose instance
 * lands in `routers`.
 */
export function createUiTestProviders<T extends RefineTestDataProvider = Pick<RefineTestDataProvider, never>>(
  defaults: UiTestProviderOptions<T> = {},
) {
  const {
    navigate: initialNavigate,
    metadata: initialMetadata,
    resources: initialResources,
    refineResources: initialRefineResources,
    router: initialRouter,
    ...refineDefaults
  } = defaults;
  const refine = createRefineTestProviders(refineDefaults);
  const routers: AnyRouter[] = [];

  function Provider({
    children,
    navigate = initialNavigate,
    metadata = initialMetadata,
    resources = initialResources ?? metadata?.resources,
    refineResources = initialRefineResources,
    providerNames = defaults.providerNames,
    router: routed = initialRouter,
    ...refineOptions
  }: UiTestProviderOptions & { children?: React.ReactNode }) {
    const [router] = React.useState(() => {
      if (!routed) return null;
      const created = createRouter({
        routeTree: createRootRoute(),
        history: createMemoryHistory({ initialEntries: routed === true ? ["/"] : [...routed.initialEntries] }),
      });
      routers.push(created);
      return created;
    });
    const schema = React.useMemo(
      () => metadata ?? (resources && schemaFieldMetadataFromDataResources(resources)),
      [metadata, resources],
    );
    const content = schema ? <ModelMetadataProvider metadata={schema}>{children}</ModelMetadataProvider> : children;
    const tree = <refine.Provider
      {...refineOptions}
      resources={refineResources ?? [...refineResourcesFromDataResources(resources ?? [])]}
      providerNames={providerNames ?? ["console", ...(resources ?? []).map((resource) => resource.schemaName)]}
    >
      <TooltipProvider delay={0}>
        <AppRuntimeProvider runtime={NO_RUNTIME}>
          <ModalsHost>
            {navigate ? <InAppLinkProvider navigate={navigate}>{content}</InAppLinkProvider> : content}
          </ModalsHost>
        </AppRuntimeProvider>
      </TooltipProvider>
    </refine.Provider>;
    return router ? <RouterContextProvider router={router}>{tree}</RouterContextProvider> : tree;
  }

  return { ...refine, Provider, routers };
}

const NO_RUNTIME = {};

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
 * production-accurate. Like the dialog, a submit runs the declared `validate`
 * first, with the same quiet call options, and submits nothing it refuses.
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
          void Promise.resolve(props.validate?.(parsed, SURFACE_RENDERS_FAILURE))
            .then(async (validation) => {
              if (validation) return;
              const result = await submitMutationDialog(props, parsed, String(props.errorFallback ?? ""));
              if (result.status === "ok") props.onSubmitted?.(result.data, parsed);
            });
        }}
      >
        <button type="submit">{buttonLabel}</button>
      </form>
    </>;
  };
}
