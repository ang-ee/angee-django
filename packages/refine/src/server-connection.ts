/** A request refused because the app's recovery check found the server unavailable. */
export class ServerUnavailableError extends Error {
  constructor() {
    super("Connection to the server was lost.");
    this.name = "ServerUnavailableError";
  }
}

/**
 * `checking` after a request failed the way an outage does; `unavailable` once
 * the app's recovery check failed as well.
 */
export type ServerConnectionStatus = "connected" | "checking" | "unavailable";

/**
 * One transport gate shared by an app's schema clients, including CSRF.
 * A network failure or gateway response leaves that request's own error intact
 * and asks for a check; only a failed check blocks requests. It never queues or
 * replays requests: the caller may have sent a mutation. TanStack Query owns the
 * check's scheduling; its answer calls `disconnect()` or `restore()`.
 */
export class ServerConnection {
  private status: ServerConnectionStatus = "connected";
  private armed: number | null = null;
  private readonly listeners = new Set<() => void>();

  getSnapshot = (): ServerConnectionStatus => this.status;

  /** When the connection last left `connected` (epoch ms); null while connected. */
  get armedAt(): number | null {
    return this.armed;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  /** The recovery check reached the server. */
  restore(): void {
    this.setStatus("connected");
  }

  /** The recovery check failed: refuse requests until `restore()`. */
  disconnect(): void {
    this.setStatus("unavailable");
  }

  private setStatus(status: ServerConnectionStatus): void {
    if (this.status === status) return;
    if (status === "connected") this.armed = null;
    else if (this.status === "connected") this.armed = Date.now();
    this.status = status;
    this.listeners.forEach(listener => listener());
  }

  private suspect(): void {
    if (this.status === "connected") this.setStatus("checking");
  }

  fetch(baseFetch: typeof globalThis.fetch): typeof globalThis.fetch {
    return async (input, init) => {
      const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
      signal?.throwIfAborted();
      if (this.status === "unavailable") throw new ServerUnavailableError();
      try {
        const response = await baseFetch(input, init);
        if ([502, 503, 504].includes(response.status)) this.suspect();
        return response;
      } catch (error) {
        // Cancellation is a caller decision, not evidence of a server outage.
        const cancelled = signal?.aborted || error instanceof Error && error.name === "AbortError";
        if (!cancelled && error instanceof TypeError) this.suspect();
        throw error;
      }
    };
  }
}
