import type { ReactNode } from "react";
import {
  AppRuntimeProvider,
  GraphQLClientProvider,
  type AngeeUrqlClientOptions,
} from "@angee/sdk";
import { ModalsHost, baseIcons, defaultWidgets } from "@angee/base";

/**
 * Shared story fixtures for data-bound views (`ListView`/`FormView`). A view that
 * fetches needs the same provider stack — modal host, a urql client over a story
 * fetch responder, and the composed runtime (base icons + widgets) — so it lives
 * here once instead of being re-hand-rolled per story. The global preview
 * decorator's client only answers `currentUser`; a data view supplies its own
 * model responder via {@link storySchema}, so it nests its own client here.
 */

/** A JSON `Response` for a story fetch responder. */
export function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    headers: { "content-type": "application/json" },
  });
}

/**
 * Build the `public` schema config for a story from its model fetch responder,
 * answering the CSRF probe uniformly so the responder only describes its model
 * queries/mutations.
 */
export function storySchema(
  fetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
): Record<string, AngeeUrqlClientOptions> {
  return {
    public: {
      url: "/graphql/public/",
      fetch: (input, init) =>
        String(input).includes("/auth/csrf/")
          ? Promise.resolve(jsonResponse({ token: "storybook" }))
          : fetch(input, init),
    },
  };
}

/**
 * Wrap a data-bound view in the modal host, a urql client over `schemas`, and the
 * composed runtime (base icons + the default widget set) — the stack every
 * fetching view needs to render in isolation.
 */
export function RuntimeFixture({
  schemas,
  children,
}: {
  schemas: Record<string, AngeeUrqlClientOptions>;
  children: ReactNode;
}): ReactNode {
  return (
    <ModalsHost>
      <GraphQLClientProvider config={schemas} schema="public">
        <AppRuntimeProvider
          runtime={{ icons: baseIcons, slots: [], widgets: defaultWidgets }}
        >
          {children}
        </AppRuntimeProvider>
      </GraphQLClientProvider>
    </ModalsHost>
  );
}
