import * as React from "react";
import type { AnyRouter } from "@tanstack/react-router";
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

import { AppShellTestProviders, type AppShellTestProvidersProps } from "./testing-shell";
import { IdempotencyKeyStore } from "./lib/idempotency-key";
import { InAppLinkProvider, type InAppNavigator } from "./lib/in-app-link";
import { TooltipProvider } from "./ui/tooltip";

import type {
  MutationDialogProps,
  MutationDialogValues,
} from "./views/form/MutationDialog";
import { submitMutationDialog } from "./views/form/MutationDialog";
import { SURFACE_RENDERS_FAILURE, formSubmitOutcome } from "./views/form/validation-errors";

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
  router?: AppShellTestProvidersProps["router"];
}

/**
 * Add resource projection and metadata context to the native Refine test harness.
 * Tooltips open as soon as the pointer enters their trigger, so a hover assertion
 * never races Base UI's wall-clock open delay; a nested `TooltipProvider` keeps
 * its own delay. Under them, the app shell's providers (`AppShellTestProviders`, from the
 * runner-free `@angee/ui/testing/shell`):
 * `ModalsHost`, an `AppRuntimeProvider` holding the runtime's idempotency keys, fresh
 * per mount, under any the test renders, and with `router` a memory router whose
 * instance lands in `routers`.
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
    const schema = React.useMemo(
      () => metadata ?? (resources && schemaFieldMetadataFromDataResources(resources)),
      [metadata, resources],
    );
    const content = schema ? <ModelMetadataProvider metadata={schema}>{children}</ModelMetadataProvider> : children;
    return <refine.Provider
      {...refineOptions}
      resources={refineResources ?? [...refineResourcesFromDataResources(resources ?? [])]}
      providerNames={providerNames ?? ["console", ...(resources ?? []).map((resource) => resource.schemaName)]}
    >
      <TooltipProvider delay={0}>
        <AppShellTestProviders router={routed} onRouter={(router) => routers.push(router)}>
          {navigate ? <InAppLinkProvider navigate={navigate}>{content}</InAppLinkProvider> : content}
        </AppShellTestProviders>
      </TooltipProvider>
    </refine.Provider>;
  }

  return { ...refine, Provider, routers };
}


/** One `focus()` call `recordFocusCalls` saw: its target, and the test DOM's best word on whether it took focus. */
export interface RecordedFocusCall {
  target: HTMLElement;
  /**
   * Whether the target could take focus at that moment, by what the test DOM shows (see
   * `recordFocusCalls`). happy-dom focuses several of the targets this refuses.
   */
  takesFocus: boolean;
}

/**
 * Record every `focus()` call until `restore`, each with `takesFocus`: false for a target that is
 * not in the document; is disabled; is a form control inside a disabled fieldset, outside that
 * fieldset's first legend; sits inside `[hidden]`, `[inert]` or a closed `details` (outside its
 * summary); has, or has an ancestor with, a computed `display: none`, or is computed
 * `visibility: hidden` or `collapse`; or is not focusable at all (no native focusability, no
 * `tabindex`, not editable, as a `contenteditable="false"` element without `tabindex` and a
 * `summary` that is not the first of a `details` are not). happy-dom focuses most of these, and
 * it computes styles only from inline styles and `<style>` elements it holds: a host hidden by an
 * app stylesheet, which these tests do not load, is a browser's to judge. So are a target in a
 * shadow tree (the walk stops at the shadow root) and a media element with controls, which a
 * browser focuses and this refuses.
 * @kind null
 * @reason Test infrastructure: the DOM's focus calls, for a rendered test.
 */
export function recordFocusCalls(): { calls: RecordedFocusCall[]; restore: () => void } {
  const calls: RecordedFocusCall[] = [];
  const focus = HTMLElement.prototype.focus;
  const focusable = (element: HTMLElement): boolean => {
    const editable = element.getAttribute("contenteditable");
    if (element.hasAttribute("tabindex") || editable === "" || editable === "true" || editable === "plaintext-only") return true;
    if (element.tagName === "INPUT") return element.getAttribute("type") !== "hidden";
    if (/^(A|AREA)$/.test(element.tagName)) return element.hasAttribute("href");
    if (element.tagName === "SUMMARY") {
      const details = element.parentElement;
      return details?.tagName === "DETAILS" && [...details.children].find((child) => child.tagName === "SUMMARY") === element;
    }
    return /^(BUTTON|SELECT|TEXTAREA|IFRAME)$/.test(element.tagName);
  };
  const takesFocus = (element: HTMLElement): boolean => {
    if (!element.isConnected || (element as HTMLButtonElement).disabled || !focusable(element)) return false;
    if (/^(hidden|collapse)$/.test(getComputedStyle(element).visibility)) return false;
    const formControl = /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(element.tagName);
    for (let node: HTMLElement | null = element; node; node = node.parentElement) {
      if (node.hasAttribute("hidden") || node.hasAttribute("inert") || getComputedStyle(node).display === "none") return false;
      const parent = node.parentElement;
      const first = (tag: string) => parent && [...parent.children].find((child) => child.tagName === tag) === node;
      if (formControl && parent?.tagName === "FIELDSET" && parent.hasAttribute("disabled") && !first("LEGEND")) return false;
      if (parent?.tagName === "DETAILS" && !parent.hasAttribute("open") && !first("SUMMARY")) return false;
    }
    return true;
  };
  HTMLElement.prototype.focus = function recorded(this: HTMLElement, options?: FocusOptions) {
    calls.push({ target: this, takesFocus: takesFocus(this) });
    focus.call(this, options);
  };
  return { calls, restore: () => { HTMLElement.prototype.focus = focus; } };
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
 * production-accurate. Like the dialog, a submit runs the declared `validate`
 * first, with the same quiet call options, and submits nothing it refuses; and a keyed one sends
 * its draft's key through the dialog's own key owner, one per double: kept through every retry
 * until an accepted answer or a spent key ends it. It has no dismissal, so it gives no draft up.
 */
export function createMutationDialogTestDouble({
  capture,
  values = {},
  submitLabel = "Submit mutation dialog",
}: MutationDialogTestDoubleOptions = {}): (
  props: MutationDialogTestDoubleProps,
) => React.ReactElement | null {
  const keys = new IdempotencyKeyStore();
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
              // A keyed dialog's submission gets its draft's key.
              const scope: string | undefined = props.idempotencyScope;
              const submit = (key?: string) => submitMutationDialog(props, parsed, String(props.errorFallback ?? ""), key);
              const result = scope === undefined ? await submit() : await keys.send(scope, submit, formSubmitOutcome);
              if (result.status === "ok") props.onSubmitted?.(result.data, parsed);
            });
        }}
      >
        <button type="submit">{buttonLabel}</button>
      </form>
    </>;
  };
}
