import { useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from "react";
import { useQuery, type QueryClient } from "@tanstack/react-query";
import type { NotificationProvider } from "@refinedev/core";
import { Dialog } from "@angee/ui";
import { LazyBoundary } from "@angee/ui/fragments/LazyBoundary";
import { useUiT } from "@angee/ui/i18n";
import { ContainerOutlet, containerHasContent } from "@angee/ui/lib/container-outlet";
import { useContainer, useRuntimeBrand, type CoreContainer } from "@angee/ui/runtime";
import { Glyph } from "@angee/ui/chrome/Glyph";
import { AnimatedThemeLogo } from "@angee/ui/theme";
import {
  createAngeeGraphQLClient,
  ServerUnavailableError,
  type AngeeHasuraClientOptions,
  type ServerConnection,
  type ServerConnectionStatus,
} from "@angee/refine";
import { parse } from "graphql";

const CONNECTION_QUERY = parse("query AngeeConnection { __typename }");
const PROBE_TIMEOUT_MS = 10_000;
const INTERRUPTED_WRITE_KEY = "angee.connection.interrupted";
export const SERVER_CONNECTION_QUERY_KEY = ["angee", "server-connection"] as const;

declare module "@angee/ui/runtime" {
  interface ContainerKinds {
    /** Connection recovery illustration (`connection.lost#artwork`). */
    artwork: ReactNode;
    /** Connection recovery guidance (`connection.lost#help`). */
    help: ReactNode;
  }
}

/** Customer artwork and recovery guidance; shared connection behavior stays app-owned.
 * @kind contribution
 */
export const CONNECTION_CONTAINERS: readonly CoreContainer[] = [
  { address: "connection.lost#artwork" },
  { address: "connection.lost#help" },
];

/** A safe public-schema read with its own deadline; never retry the operation that detected the outage. */
export function createServerProbe(schema: AngeeHasuraClientOptions): (signal: AbortSignal) => Promise<void> {
  return async (signal) => {
    signal.throwIfAborted();
    const baseFetch = schema.fetch ?? globalThis.fetch;
    const deadline = new AbortController();
    const abort = () => { deadline.abort(signal.reason); };
    signal.addEventListener("abort", abort);
    const timeout = setTimeout(abort, PROBE_TIMEOUT_MS);
    try {
      const client = createAngeeGraphQLClient({
        ...schema,
        connection: undefined,
        fetch: (input, init) => baseFetch(input, { ...init, signal: deadline.signal, cache: "no-store" }),
      });
      await client.request(CONNECTION_QUERY);
    } finally {
      clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
    }
  };
}

/**
 * One native recovery query for the whole app. A request that fails like an
 * outage starts it; the dialog opens only when it fails too. Children stay
 * mounted so an interrupted connection does not discard a person's page or edits.
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
  const status = useSyncExternalStore<ServerConnectionStatus>(connection.subscribe, connection.getSnapshot, () => "connected");
  const unavailable = status === "unavailable";
  useQuery({
    queryKey: SERVER_CONNECTION_QUERY_KEY,
    enabled: status !== "connected",
    queryFn: async ({ signal }) => {
      try {
        await probe(signal);
      } catch (error) {
        if (!signal.aborted) connection.disconnect();
        throw error;
      }
      signal.throwIfAborted();
      const lost = connection.getSnapshot() === "unavailable";
      connection.restore();
      if (lost) onRestored();
      return true;
    },
    retry: true,
    retryDelay: attempt => Math.min(5_000 * 2 ** attempt, 30_000),
    // The probe's answer is the outage signal; the browser's online flag must not pause it.
    networkMode: "always",
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  }, queryClient);
  useEffect(() => {
    if (unavailable) onLost();
  }, [unavailable, onLost]);

  return <>{children}<ConnectionLostDialog open={unavailable} /></>;
}

/**
 * Refine notifications during an outage. Error toasts wait while the server is
 * unavailable, because recovery refreshes every read the outage refused; a
 * write it refused is reported once the server is back, because it is never
 * replayed.
 * @kind null
 * @reason App provider infrastructure: adapts Refine notifications to the shared connection state.
 */
export function useConnectionNotifications(
  connection: ServerConnection,
  queryClient: QueryClient,
  notifications: NotificationProvider,
): NotificationProvider {
  const t = useUiT();
  const interrupted = useRef(false);
  useEffect(() => queryClient.getMutationCache().subscribe((event) => {
    if (event.type === "updated" && event.action.type === "error" && event.action.error instanceof ServerUnavailableError) {
      interrupted.current = true;
    }
  }), [queryClient]);
  useEffect(() => connection.subscribe(() => {
    if (connection.getSnapshot() !== "connected" || !interrupted.current) return;
    interrupted.current = false;
    notifications.open({
      key: INTERRUPTED_WRITE_KEY,
      type: "error",
      message: t("connection.interrupted"),
      description: t("connection.interruptedDescription"),
    });
  }), [connection, notifications, t]);
  return useMemo(() => ({
    ...notifications,
    open: (params) => {
      if (params.type !== "error" || connection.getSnapshot() !== "unavailable") notifications.open(params);
    },
  }), [connection, notifications]);
}

/**
 * @kind fragment
 * @owner ConnectionBoundary
 */
export function ConnectionLostDialog({ open }: { open: boolean }): ReactNode {
  const t = useUiT();
  const artwork = useContainer("connection.lost#artwork");
  const help = useContainer("connection.lost#help");
  const brand = useRuntimeBrand();
  const defaultArtwork = brand ? <Glyph name={brand.mark} className="size-24 text-accent" />
    : <AnimatedThemeLogo size={160} animation="rotate-slide" />;
  // The dialog renders above the app's providers and error boundaries: a
  // contribution that suspends, throws or needs their context degrades here.
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
              {containerHasContent(artwork)
                ? <LazyBoundary pending={defaultArtwork}><ContainerOutlet entries={artwork} /></LazyBoundary>
                : defaultArtwork}
            </div>
            <Dialog.Title className="max-w-2xl text-3xl font-medium tracking-tight sm:text-5xl">{t("connection.title")}</Dialog.Title>
            <Dialog.Description className="mt-5 max-w-md text-base leading-relaxed text-fg-muted">{t("connection.description")}</Dialog.Description>
            <div role="status" className="mt-8 inline-flex items-center gap-3 rounded-full border border-border-subtle bg-sheet px-5 py-3 text-sm text-fg">
              <span aria-hidden="true" className="size-2 rounded-full bg-accent motion-safe:animate-pulse" />
              {t("connection.retrying")}
            </div>
            <p className="mt-5 max-w-sm text-xs leading-relaxed text-fg-muted">{t("connection.resume")}</p>
            {containerHasContent(help)
              ? <div className="mt-6 max-w-md text-sm text-fg-muted"><LazyBoundary pending={null}><ContainerOutlet entries={help} /></LazyBoundary></div>
              : null}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
