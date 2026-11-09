/** An unavailable server is handled by the app's single recovery query. */
export class ServerUnavailableError extends Error {
  constructor() {
    super("Connection to the server was lost.");
    this.name = "ServerUnavailableError";
  }
}

/**
 * One transport gate shared by an app's schema clients, including CSRF.
 * It never queues or replays requests: the caller may have sent a mutation.
 * TanStack Query owns recovery scheduling; a successful probe calls restore().
 */
export class ServerConnection {
  private unavailable = false;
  private readonly listeners = new Set<() => void>();

  getSnapshot = (): boolean => this.unavailable;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  restore(): void {
    this.setUnavailable(false);
  }

  disconnect(): void {
    this.setUnavailable(true);
  }

  private setUnavailable(value: boolean): void {
    if (this.unavailable === value) return;
    this.unavailable = value;
    this.listeners.forEach(listener => listener());
  }

  fetch(baseFetch: typeof globalThis.fetch): typeof globalThis.fetch {
    return async (input, init) => {
      const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
      signal?.throwIfAborted();
      if (this.unavailable) throw new ServerUnavailableError();
      try {
        const response = await baseFetch(input, init);
        if ([502, 503, 504].includes(response.status)) {
          throw new ServerUnavailableError();
        }
        return response;
      } catch (error) {
        // Cancellation is a caller decision, not evidence of a server outage.
        if (signal?.aborted || error instanceof Error && error.name === "AbortError") throw error;
        if (error instanceof ServerUnavailableError || error instanceof TypeError) {
          this.setUnavailable(true);
          throw new ServerUnavailableError();
        }
        throw error;
      }
    };
  }
}
