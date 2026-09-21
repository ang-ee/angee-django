import * as React from "react";
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

import type {
  MutationDialogProps,
  MutationDialogValues,
} from "./views/form/MutationDialog";

type UiModule = typeof import("./index");
export type UiTestDoubles = Partial<Record<keyof UiModule, unknown>>;

export interface UiTestProviderOptions<T extends RefineTestDataProvider = RefineTestDataProvider>
  extends Omit<RefineTestProviderOptions<T>, "resources"> {
  metadata?: SchemaFieldMetadata;
  resources?: readonly DataResourceMetadata[];
  /** Override the projected registry when a case exercises explicit routes or no registration. */
  refineResources?: RefineTestProviderOptions["resources"];
}

/** Add resource projection and metadata context to the native Refine test harness. */
export function createUiTestProviders<T extends RefineTestDataProvider = Pick<RefineTestDataProvider, never>>(
  defaults: UiTestProviderOptions<T> = {},
) {
  const {
    metadata: initialMetadata,
    resources: initialResources,
    refineResources: initialRefineResources,
    ...refineDefaults
  } = defaults;
  const refine = createRefineTestProviders(refineDefaults);

  function Provider({
    children,
    metadata = initialMetadata,
    resources = initialResources ?? metadata?.resources,
    refineResources = initialRefineResources,
    providerNames = defaults.providerNames,
    ...refineOptions
  }: UiTestProviderOptions & { children?: React.ReactNode }) {
    const schema = React.useMemo(
      () => metadata ?? (resources && schemaFieldMetadataFromDataResources(resources)),
      [metadata, resources],
    );
    return <refine.Provider
      {...refineOptions}
      resources={refineResources ?? [...refineResourcesFromDataResources(resources ?? [])]}
      providerNames={providerNames ?? ["console", ...(resources ?? []).map((resource) => resource.schemaName)]}
    >
      {schema ? <ModelMetadataProvider metadata={schema}>{children}</ModelMetadataProvider> : children}
    </refine.Provider>;
  }

  return { ...refine, Provider };
}

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
