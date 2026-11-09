import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { onlineManager, useQuery, type QueryClient } from "@tanstack/react-query";
import { AngeeLogoCube, PRESETS } from "@angee/logo-react";
import "@angee/logo-react/style.css";
import { Dialog } from "@angee/ui";
import { useUiT } from "@angee/ui/i18n";
import { useMediaQuery } from "@angee/ui/lib";
import { ContainerOutlet, containerHasContent } from "@angee/ui/lib/container-outlet";
import { useContainer, useRuntimeBrand, type CoreContainer } from "@angee/ui/runtime";
import { Glyph } from "@angee/ui/chrome/Glyph";
import { createAngeeGraphQLClient, type AngeeHasuraClientOptions, type ServerConnection } from "@angee/refine";
import { parse } from "graphql";

const CONNECTION_QUERY = parse("query AngeeConnection { __typename }");
export const SERVER_CONNECTION_QUERY_KEY = ["angee", "server-connection"] as const;

/** Customer artwork and recovery guidance; shared connection behavior stays app-owned.
 * @kind contribution
 */
export const CONNECTION_CONTAINERS: readonly CoreContainer[] = [
  { address: "connection.lost#artwork" },
  { address: "connection.lost#help" },
];

/** A safe public-schema read; never retry the operation that detected the outage. */
export function createServerProbe(schema: AngeeHasuraClientOptions): (signal: AbortSignal) => Promise<void> {
  return async (signal) => {
    const baseFetch = schema.fetch ?? globalThis.fetch;
    const deadline = AbortSignal.any([signal, AbortSignal.timeout(10_000)]);
    const client = createAngeeGraphQLClient({
      ...schema,
      connection: undefined,
      fetch: (input, init) => baseFetch(input, { ...init, signal: deadline, cache: "no-store" }),
    });
    await client.request(CONNECTION_QUERY);
  };
}

/**
 * One native recovery query for the whole app. Children stay mounted so an
 * interrupted connection does not discard a person's current page or edits.
 * @kind null
 * @reason App provider infrastructure: connects transport availability to native query recovery and modal feedback.
 */
export function ConnectionBoundary({ connection, queryClient, probe, onRestored, onLost, children }: {
  connection: ServerConnection;
  queryClient: QueryClient;
  probe: (signal: AbortSignal) => Promise<void>;
  onRestored: () => void;
  onLost: () => void;
  children: ReactNode;
}): ReactNode {
  const unavailable = useSyncExternalStore(connection.subscribe, connection.getSnapshot, () => false);
  useQuery({
    queryKey: SERVER_CONNECTION_QUERY_KEY,
    enabled: unavailable,
    queryFn: async ({ signal }) => {
      await probe(signal);
      signal.throwIfAborted();
      connection.restore();
      onRestored();
      return true;
    },
    retry: true,
    retryDelay: attempt => Math.min(5_000 * 2 ** attempt, 30_000),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  }, queryClient);
  useEffect(() => {
    if (unavailable) onLost();
  }, [unavailable, onLost]);
  useEffect(() => onlineManager.subscribe(online => {
    if (!online) connection.disconnect();
  }), [connection]);

  return <>{children}<ConnectionLostDialog open={unavailable} /></>;
}

/**
 * @kind fragment
 * @owner ConnectionBoundary
 */
export function ConnectionLostDialog({ open }: { open: boolean }): ReactNode {
  const t = useUiT();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const artwork = useContainer("connection.lost#artwork");
  const help = useContainer("connection.lost#help");
  const brand = useRuntimeBrand();
  return (
    <Dialog.Root open={open} disablePointerDismissal>
      <Dialog.Portal>
        <Dialog.Backdrop className="bg-canvas/95 backdrop-blur-xl" />
        <Dialog.Content
          placement="center"
          className="isolate h-dvh max-h-dvh w-screen max-w-none rounded-none border-0 bg-canvas shadow-none"
        >
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_38%,var(--color-accent-soft),transparent_65%)]" />
          <div className="relative flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-10 text-center">
            <div className="mb-6 text-xs font-medium uppercase tracking-[0.3em] text-fg-muted">{t("connection.eyebrow")}</div>
            <div aria-hidden="true" className="relative mb-8 flex size-56 shrink-0 items-center justify-center sm:size-72">
              <div className="absolute inset-0 rounded-full border border-border-subtle" />
              <div className="absolute inset-6 rounded-full border border-border-subtle/60" />
              {containerHasContent(artwork) ? <ContainerOutlet entries={artwork} />
                : brand ? <Glyph name={brand.mark} className="size-24 text-accent" />
                  : <AngeeLogoCube size={32} gap={2} leftColor="#14b8a6" rightColor={PRESETS.gold.colors.top}
                    baseDark="#08111f" animationType={reducedMotion ? "none" : "rotate-slide"} animationSpeed={18} />}
            </div>
            <Dialog.Title className="max-w-2xl text-3xl font-medium tracking-tight sm:text-5xl">{t("connection.title")}</Dialog.Title>
            <Dialog.Description className="mt-5 max-w-md text-base leading-relaxed text-fg-muted">{t("connection.description")}</Dialog.Description>
            <div role="status" className="mt-8 inline-flex items-center gap-3 rounded-full border border-border-subtle bg-sheet px-5 py-3 text-sm text-fg">
              <span aria-hidden="true" className="size-2 rounded-full bg-accent motion-safe:animate-pulse" />
              {t("connection.retrying")}
            </div>
            <p className="mt-5 max-w-sm text-xs leading-relaxed text-fg-muted">{t("connection.resume")}</p>
            {containerHasContent(help) ? <div className="mt-6 max-w-md text-sm text-fg-muted"><ContainerOutlet entries={help} /></div> : null}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
