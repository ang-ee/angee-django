import { ModelMetadataProvider, defineAngeeSchemaMetadata, schemaFieldMetadataFromAngeeSchemaMetadata, type AngeeSchemaMetadata, } from "@angee/metadata";
import {
  useMemo, type ReactNode } from "react";
import { Refine } from "@refinedev/core";
import {
  AppRuntimeProvider,
  baseIcons,
  defaultWidgets,
  type AppRuntime,
} from "@angee/ui";
import { ActiveGraphQLSchemaProvider, } from "@angee/metadata";
import {
  createAngeeHasuraDataProviders, type AngeeHasuraSchemaConfig, } from "@angee/refine";
import { ModalsHost } from "@angee/ui";

/**
 * Shared story fixtures for data-bound views (`ListView`/`FormView`). A view that
 * fetches needs the same provider stack — modal host, refine data provider over a
 * story fetch responder, generated resource metadata, and the composed runtime
 * (base icons + widgets) — so it lives here once instead of being re-hand-rolled
 * per story.
 */

type StorySchemaConfig = AngeeHasuraSchemaConfig & {
  metadata?: AngeeSchemaMetadata;
};

export function RuntimeRegistryFixture({
  children,
  runtime = {},
}: {
  children: ReactNode;
  runtime?: Partial<AppRuntime>;
}): ReactNode {
  return (
    <AppRuntimeProvider
      runtime={{ icons: baseIcons, widgets: defaultWidgets, ...runtime }}
    >
      {children}
    </AppRuntimeProvider>
  );
}

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
): Record<string, StorySchemaConfig> {
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
 * Wrap a data-bound view in the modal host, refine data providers over `schemas`,
 * active schema metadata, and the composed runtime — the stack every fetching
 * view needs to render in isolation.
 */
export function RuntimeFixture({
  schemas,
  children,
}: {
  schemas: Record<string, StorySchemaConfig>;
  children: ReactNode;
}): ReactNode {
  const normalized = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(schemas).map(([name, schema]) => {
          const metadata = schema.metadata
            ? defineAngeeSchemaMetadata(schema.metadata)
            : undefined;
          return [
            name,
            {
              ...schema,
              ...(metadata ? { metadata } : {}),
            },
          ];
        }),
      ) as Record<string, StorySchemaConfig>,
    [schemas],
  );
  const dataProvider = useMemo(
    () => createAngeeHasuraDataProviders(normalized, "public"),
    [normalized],
  );
  const fieldMetadata = useMemo(
    () =>
      schemaFieldMetadataFromAngeeSchemaMetadata(normalized.public?.metadata),
    [normalized],
  );
  return (
    <ModalsHost>
      <Refine
        dataProvider={dataProvider}
        options={{ syncWithLocation: false }}
      >
        <ActiveGraphQLSchemaProvider schema="public">
          <ModelMetadataProvider metadata={fieldMetadata}>
            <RuntimeRegistryFixture runtime={{ slots: [] }}>
              {children}
            </RuntimeRegistryFixture>
          </ModelMetadataProvider>
        </ActiveGraphQLSchemaProvider>
      </Refine>
    </ModalsHost>
  );
}
