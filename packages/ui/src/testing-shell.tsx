// The app shell's providers for a test or a story (`@angee/ui/testing/shell`). Free of any test
// runner by construction, as stories bundle it: what needs one (spied data providers) stays in
// `@angee/ui/testing`.
import * as React from "react";
import {
  RouterContextProvider,
  createMemoryHistory,
  createRootRoute,
  createRouter,
  type AnyRouter,
} from "@tanstack/react-router";

import { ModalsHost } from "./feedback/ModalsHost";
import { AppRuntimeProvider, type AppRuntime } from "./runtime/runtime";

export interface AppShellTestProvidersProps {
  children?: React.ReactNode;
  /** The runtime the app shell provides; by default the app's defaults. */
  runtime?: Partial<AppRuntime>;
  /**
   * Render under a memory router at these entries (`true`: `/`), as an app page is: a form
   * dialog guards route moves while it holds work.
   */
  router?: true | { initialEntries: readonly string[] };
  /** Receives the memory router `router` creates, once per mount. */
  onRouter?: (router: AnyRouter) => void;
}

const NO_RUNTIME = {};

/**
 * What every rendered surface has from the app shell, for a test or a story: the root
 * `AppRuntimeProvider` (with it the runtime's idempotency keys, fresh per mount), `ModalsHost`,
 * which answers the confirmations a surface asks, and with `router` a memory router.
 * `@angee/ui/testing`'s `createUiTestProviders` and `@angee/app/testing`'s
 * `ShellPageTestProviders` both render it.
 * @kind null
 * @reason Test infrastructure: the app shell's providers.
 */
export function AppShellTestProviders({ children, runtime = NO_RUNTIME, router: routed, onRouter }: AppShellTestProvidersProps): React.ReactElement {
  const [router] = React.useState(() => {
    if (!routed) return null;
    const created = createRouter({
      routeTree: createRootRoute(),
      history: createMemoryHistory({ initialEntries: routed === true ? ["/"] : [...routed.initialEntries] }),
    });
    onRouter?.(created);
    return created;
  });
  const shell = <AppRuntimeProvider runtime={runtime}>
    <ModalsHost>{children}</ModalsHost>
  </AppRuntimeProvider>;
  return router ? <RouterContextProvider router={router}>{shell}</RouterContextProvider> : shell;
}
