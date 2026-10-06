// @vitest-environment happy-dom

import type { DocumentData } from "@angee/refine";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { OperatorConnectionQuery } from "./documents";
import { createOperatorClient } from "./operator-client";
import { operatorToken } from "./operator-token";
import { OperatorTransportProvider, useOperatorConnectionState, useOperatorQuery } from "./transport";
import { SERVICE_ENDPOINT_QUERY } from "./documents.daemon";

const bridge = vi.hoisted(() => ({
  query: {
    data: undefined as DocumentData<typeof OperatorConnectionQuery> | undefined,
    error: null as Error | null,
    isFetching: false,
  },
  operatorReads: [] as { enabled?: boolean; dataProviderName?: string }[],
  t: (key: string) => key,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (_document: unknown, _variables: unknown, options: { enabled?: boolean; dataProviderName?: string } = {}) => {
    if (options.dataProviderName === "operator") bridge.operatorReads.push(options);
    return bridge.query;
  },
}));

vi.mock("../i18n", () => ({ useOperatorT: () => bridge.t }));

vi.mock("./operator-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./operator-client")>()),
  createOperatorClient: vi.fn(() => ({ subscribe: () => () => undefined })),
}));

const clients: QueryClient[] = [];

function connectionData(token = "first-token"): DocumentData<typeof OperatorConnectionQuery> {
  return {
    operator_connection: {
      endpoint: "http://daemon.test/graphql/",
      token,
      restart_job: "deps",
    },
  };
}

function renderTransport(children: ReactNode = null) {
  const client = new QueryClient();
  clients.push(client);
  return render(
    <OperatorTransportProvider>{children}</OperatorTransportProvider>,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    },
  );
}

// A newly mounted page starts its ready-gated request in a child effect.
// Observe again when the provider publishes the next committed state.
function BearerProbe({ seen }: { seen: (string | null)[] }) {
  const state = useOperatorConnectionState();
  useEffect(() => {
    if (state.kind === "ready") seen.push(operatorToken.get());
  }, [state, seen]);
  return null;
}

beforeEach(() => {
  bridge.query.data = connectionData();
  bridge.query.error = null;
  bridge.query.isFetching = false;
  bridge.operatorReads = [];
  bridge.t = (key: string) => key;
  vi.mocked(createOperatorClient).mockClear();
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("OperatorTransportProvider bearer lifetime", () => {
  test("keeps the bearer while a new translator and a page commit together", () => {
    const seen: (string | null)[] = [];
    const view = renderTransport();
    expect(operatorToken.get()).toBe("first-token");

    bridge.t = (key: string) => `translated:${key}`;
    view.rerender(
      <OperatorTransportProvider>
        <BearerProbe seen={seen} />
      </OperatorTransportProvider>,
    );

    expect(seen).toEqual(["first-token", "first-token"]);
    expect(operatorToken.get()).toBe("first-token");
    expect(createOperatorClient).toHaveBeenCalledTimes(1);
  });

  test.each([true, false])("keeps the bearer when fetching becomes %s as a page mounts", (isFetching) => {
    bridge.query.isFetching = !isFetching;
    const seen: (string | null)[] = [];
    const view = renderTransport();
    expect(operatorToken.get()).toBe("first-token");

    bridge.query.isFetching = isFetching;
    view.rerender(
      <OperatorTransportProvider>
        <BearerProbe seen={seen} />
      </OperatorTransportProvider>,
    );

    expect(seen).toEqual(["first-token", "first-token"]);
    expect(operatorToken.get()).toBe("first-token");
    expect(createOperatorClient).toHaveBeenCalledTimes(1);
  });

  test("rotates from the old bearer to the new bearer without an empty value", () => {
    const seen: (string | null)[] = [];
    const view = renderTransport();
    bridge.query.data = connectionData("rotated-token");

    view.rerender(
      <OperatorTransportProvider>
        <BearerProbe seen={seen} />
      </OperatorTransportProvider>,
    );

    expect(seen).toEqual(["first-token", "rotated-token"]);
    expect(operatorToken.get()).toBe("rotated-token");
    expect(vi.mocked(createOperatorClient).mock.calls.map(([connection]) => connection.token))
      .toEqual(["first-token", "rotated-token"]);
  });

  test("publishes readiness only after installing the bearer", () => {
    const seen: (string | null)[] = [];

    renderTransport(<BearerProbe seen={seen} />);

    expect(seen).toEqual(["first-token"]);
  });

  test.each(["loading", "not-configured", "error"] as const)("clears the bearer when the connection becomes %s", (kind) => {
    const view = renderTransport();
    expect(operatorToken.get()).toBe("first-token");
    if (kind === "loading") {
      bridge.query.data = undefined;
      bridge.query.isFetching = true;
    } else if (kind === "not-configured") {
      bridge.query.data = { operator_connection: null };
    } else {
      bridge.query.error = new Error("Connection unavailable");
    }

    view.rerender(<OperatorTransportProvider>{null}</OperatorTransportProvider>);

    expect(operatorToken.get()).toBeNull();
  });

  test("clears the current bearer on unmount after a rotation", () => {
    const view = renderTransport();
    bridge.query.data = connectionData("rotated-token");
    view.rerender(<OperatorTransportProvider>{null}</OperatorTransportProvider>);
    expect(operatorToken.get()).toBe("rotated-token");

    view.unmount();

    expect(operatorToken.get()).toBeNull();
  });
});

describe("useOperatorQuery", () => {
  function OperatorRead({ enabled }: { enabled?: boolean }) {
    useOperatorQuery(SERVICE_ENDPOINT_QUERY, { name: "django" }, enabled === undefined ? {} : { enabled });
    return null;
  }

  test("sends nothing until the connection is ready, then reads with the bearer", () => {
    bridge.query.data = undefined;
    bridge.query.isFetching = true;
    const view = renderTransport(<OperatorRead />);
    expect(bridge.operatorReads.every((read) => read.enabled === false)).toBe(true);

    bridge.query.data = connectionData();
    bridge.query.isFetching = false;
    view.rerender(<OperatorTransportProvider><OperatorRead /></OperatorTransportProvider>);
    expect(bridge.operatorReads.at(-1)?.enabled).toBe(true);
    expect(operatorToken.get()).toBe("first-token");
  });

  test("a caller's own condition still holds once connected", () => {
    renderTransport(<OperatorRead enabled={false} />);
    expect(bridge.operatorReads.at(-1)?.enabled).toBe(false);
  });
});
