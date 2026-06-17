import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  LogStream,
} from "@angee/base";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "urql";

import { useOperatorT } from "../../i18n";
import { useOperatorConnection, useOperatorSubscription } from "../../data/transport";

const HISTORY_LIMIT = 500;
const MAX_LIVE_LINES = 2000;

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
  historyQuery: string;
  historyField: string;
  streamSubscription: string;
  streamField: string;
}): DaemonLogStream {
  const [history] = useQuery<Record<string, string | null>>({
    query: historyQuery,
    variables: { name: name ?? "", limit: HISTORY_LIMIT },
    pause: !name,
  });
  const [live, setLive] = useState<readonly string[]>([]);
  const stream = useOperatorSubscription<Record<string, string | null>, { name: string }>(
    streamSubscription,
    { name: name ?? "" },
    {
      enabled: Boolean(name),
      onData: (value) => {
        const line = value[streamField];
        if (line == null) return;
        setLive((prev) => {
          const next = [...prev, line];
          return next.length > MAX_LIVE_LINES ? next.slice(-MAX_LIVE_LINES) : next;
        });
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
    streaming: stream.fetching && stream.error == null,
  };
}

const SERVICE_LOG_TAIL = 500;
const RECONNECT_DELAY_MS = 2000;

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
 * line as a `LogLine` (exact per-service attribution + best-effort level). It
 * reconnects after a drop without re-replaying the backlog and surfaces the
 * connection state through {@link DaemonLogStream}.
 */
export function useServiceLogStream(name: string | undefined): DaemonLogStream {
  const { endpoint, token } = useOperatorConnection();
  const [lines, setLines] = useState<readonly string[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!name) return;
    setLines([]);
    setStreaming(false);
    setError(null);

    let disposed = false;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let connectedOnce = false;

    const open = (): void => {
      if (disposed) return;
      const ws = new WebSocket(
        serviceLogsSocketUrl(endpoint, token, name, connectedOnce ? 0 : SERVICE_LOG_TAIL),
      );
      socket = ws;
      ws.onopen = () => {
        if (disposed) return;
        connectedOnce = true;
        setStreaming(true);
        setError(null);
      };
      ws.onmessage = (event: MessageEvent) => {
        if (disposed) return;
        const line = parseLogFrame(event.data);
        if (line == null) return;
        setLines((prev) => {
          const next = [...prev, line];
          return next.length > MAX_LIVE_LINES ? next.slice(-MAX_LIVE_LINES) : next;
        });
      };
      ws.onerror = () => {
        if (disposed) return;
        setError(new Error("Log stream connection error"));
      };
      ws.onclose = () => {
        if (disposed) return;
        setStreaming(false);
        timer = setTimeout(open, RECONNECT_DELAY_MS);
      };
    };
    open();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [endpoint, token, name]);

  return { lines, error, streaming };
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
  return <LogPanel logs={logs} title={title ?? t("operator.services.detail.logs")} />;
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
    ? { tone: "danger" as const, label: t("operator.logs.error") }
    : logs.streaming
      ? { tone: "success" as const, label: t("operator.logs.live") }
      : { tone: "neutral" as const, label: t("operator.logs.connecting") };

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
          emptyMessage={t("operator.logs.empty")}
        />
      </CardContent>
    </Card>
  );
}
