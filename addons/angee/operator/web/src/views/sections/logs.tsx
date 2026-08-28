import { Alert, Badge, Card, CardContent, CardHeader, CardTitle, LogStream } from "@angee/ui";
import {
  isFatalGraphQLWsCloseCode, useAuthoredQuery } from "@angee/refine";
import type { DocumentData } from "@angee/refine";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { OPERATOR_PROVIDER } from "../../data/operator-provider";
import { useOperatorT } from "../../i18n";
import { useOperatorConnection, useOperatorSubscription } from "../../data/transport";
import type {
  WORKSPACE_LOGS_QUERY,
  WORKSPACE_LOGS_SUBSCRIPTION,
} from "../../data/documents.daemon";

const HISTORY_LIMIT = 500;
const MAX_LIVE_LINES = 2000;

/** Append a line to a live tail, keeping only the last {@link MAX_LIVE_LINES}. */
function appendCapped(prev: readonly string[], line: string): readonly string[] {
  const next = [...prev, line];
  return next.length > MAX_LIVE_LINES ? next.slice(-MAX_LIVE_LINES) : next;
}

export interface DaemonLogStream {
  lines: readonly string[];
  /** The subscription/history error, if the stream failed to connect. */
  error: Error | null;
  /** The subscription is open and receiving. */
  streaming: boolean;
}

/**
 * A daemon log tail: the one-shot history query for first paint, then the live
 * subscription (v0.6 streams these line-by-line). The subscription's `onData`
 * accumulates each emission so none are dropped; its error/connection state is
 * surfaced so an empty pane is never ambiguous (idle service vs failed stream).
 */
export function useDaemonLogStream({
  name,
  historyQuery,
  historyField,
  streamSubscription,
  streamField,
}: {
  name: string | undefined;
  historyQuery: typeof WORKSPACE_LOGS_QUERY;
  historyField: keyof DocumentData<typeof WORKSPACE_LOGS_QUERY> & string;
  streamSubscription: typeof WORKSPACE_LOGS_SUBSCRIPTION;
  streamField: keyof DocumentData<typeof WORKSPACE_LOGS_SUBSCRIPTION> & string;
}): DaemonLogStream {
  const history = useAuthoredQuery(
    historyQuery,
    { name: name ?? "", limit: HISTORY_LIMIT },
    { dataProviderName: OPERATOR_PROVIDER, enabled: Boolean(name) },
  );
  const [live, setLive] = useState<readonly string[]>([]);
  const stream = useOperatorSubscription(
    streamSubscription,
    { name: name ?? "" },
    {
      enabled: Boolean(name),
      onData: (value) => {
        const line = value[streamField];
        if (line == null) return;
        setLive((prev) => appendCapped(prev, line));
      },
    },
  );

  const lines = useMemo(() => {
    const text = history.data?.[historyField] ?? "";
    const historyLines = text === "" ? [] : text.replace(/\n$/, "").split("\n");
    return [...historyLines, ...live];
  }, [history.data, historyField, live]);

  return {
    lines,
    error: stream.error ?? history.error ?? null,
    // "Live" means a frame has arrived — the subscription's `fetching` only means
    // it was requested, which would read Live before the socket connects.
    streaming: stream.data != null && stream.error == null,
  };
}

const SERVICE_LOG_TAIL = 500;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30_000;

// Build the structured per-service log socket URL from the same-origin daemon
// endpoint: swap the graphql path for the logs-stream path, http→ws, carrying the
// operator bearer (it passes the admin/operator tier) and a `tail` backlog.
function serviceLogsSocketUrl(
  endpoint: string,
  token: string,
  name: string,
  tail: number,
): string {
  const url = new URL(endpoint, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/graphql\/?$/, "")}/services/${encodeURIComponent(name)}/logs/stream`;
  url.search = `tail=${tail}&token=${encodeURIComponent(token)}`;
  return url.toString();
}

// One `LogLine` frame → a display line. Prefixes the best-effort level when
// present; falls back to the raw text for a non-JSON frame.
function parseLogFrame(data: unknown): string | null {
  if (typeof data !== "string") return null;
  try {
    const frame = JSON.parse(data) as { message?: unknown; level?: unknown };
    if (typeof frame.message !== "string") return null;
    const level = typeof frame.level === "string" ? frame.level : null;
    return level ? `${level.toUpperCase().padEnd(5)} ${frame.message}` : frame.message;
  } catch {
    return data;
  }
}

/**
 * A service's live logs over the v0.6 structured `/services/{name}/logs/stream`
 * WebSocket: the socket replays `tail` backlog then follows live, framing each
 * line as a `LogLine` (exact per-service attribution + best-effort level). A
 * transient drop reconnects with capped backoff (re-following without re-replaying
 * the backlog); a fatal close is terminal. The connection token is read from a ref
 * so its 15-minute rotation hot-swaps on the next reconnect rather than tearing
 * down the live socket and wiping the on-screen buffer.
 */
export function useServiceLogStream(name: string | undefined): DaemonLogStream {
  const { endpoint, token } = useOperatorConnection();
  const tokenRef = useRef(token);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  const [lines, setLines] = useState<readonly string[]>([]);
  const [status, setStatus] = useState<"connecting" | "live" | "error">("connecting");
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!name) return;
    setLines([]);
    setStatus("connecting");
    setError(null);

    let disposed = false;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let connectedOnce = false;
    let attempt = 0;

    const open = (): void => {
      if (disposed) return;
      const ws = new WebSocket(
        serviceLogsSocketUrl(endpoint, tokenRef.current, name, connectedOnce ? 0 : SERVICE_LOG_TAIL),
      );
      socket = ws;
      ws.onopen = () => {
        if (disposed) return;
        connectedOnce = true;
        attempt = 0;
        setStatus("live");
        setError(null);
      };
      ws.onmessage = (event: MessageEvent) => {
        if (disposed) return;
        const line = parseLogFrame(event.data);
        if (line != null) setLines((prev) => appendCapped(prev, line));
      };
      // An error is always followed by a close; `onclose` owns recovery, so there
      // is no separate `onerror` (which would double-render the same failure).
      ws.onclose = (event: CloseEvent) => {
        if (disposed) return;
        socket = null;
        if (event.code !== 1000 && isFatalGraphQLWsCloseCode(event.code)) {
          setStatus("error");
          setError(new Error(`Log stream closed (${event.code})`));
          return;
        }
        setStatus("connecting");
        attempt += 1;
        const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS);
        timer = setTimeout(open, delay);
      };
    };
    open();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [endpoint, name]);

  return { lines, error, streaming: status === "live" };
}

/**
 * A drop-in live service-log panel: the structured `/logs/stream` WebSocket tail
 * + the connection-status panel. The one service-logs surface — the operator
 * console's service detail and any addon that provisions a service (the agents
 * console) compose this rather than re-plumbing a log subscription.
 */
export function ServiceLogs({
  name,
  title,
}: {
  name: string | undefined;
  title?: ReactNode;
}): ReactNode {
  const t = useOperatorT();
  const logs = useServiceLogStream(name);
  return <LogPanel logs={logs} title={title ?? t("services.detail.logs")} />;
}

/** A titled log card with a connection-status badge and the {@link LogStream} tail. */
export function LogPanel({
  logs,
  title,
}: {
  logs: DaemonLogStream;
  title: ReactNode;
}): ReactNode {
  const t = useOperatorT();
  const status = logs.error
    ? { tone: "danger" as const, label: t("logs.error") }
    : logs.streaming
      ? { tone: "success" as const, label: t("logs.live") }
      : { tone: "neutral" as const, label: t("logs.connecting") };

  return (
    <Card className="flex min-h-0 flex-1 flex-col">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{title}</CardTitle>
        <Badge density="compact" shape="pill" tone={status.tone}>
          {status.label}
        </Badge>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-2">
        {logs.error ? <Alert tone="danger">{logs.error.message}</Alert> : null}
        <LogStream
          lines={logs.lines}
          className="min-h-64 flex-1"
          emptyContent={t("logs.empty")}
        />
      </CardContent>
    </Card>
  );
}
