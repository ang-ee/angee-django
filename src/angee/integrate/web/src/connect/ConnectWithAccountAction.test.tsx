// @vitest-environment happy-dom

import { fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { DescriptorField, DescriptorFieldControlProps } from "@angee/ui";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  connectAnother: vi.fn(),
  dialogProps: null as Record<string, unknown> | null,
  query: vi.fn(),
  refetch: vi.fn(),
  roles: [] as string[],
}));

vi.mock("@angee/refine", async (original) => ({
  ...await original<object>(),
  useAuthoredQuery: (document: unknown, variables: unknown, options: unknown) => {
    mocks.query(document, variables, options);
    return { data: { facts: "loaded" }, refetch: mocks.refetch };
  },
}));

vi.mock("./ConnectAccountButton", () => ({
  ConnectAccountButton: (props: { label?: string }) => {
    mocks.connectAnother(props);
    return <span>{props.label}</span>;
  },
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/ui")>();
  const { createMutationDialogTestDouble } = await import("@angee/ui/testing");
  return {
    ...original,
    useRouteHref: () => Object.assign((name: string) => `/routes/${name}`, {
      maybe: (name: string) => `/routes/${name}`,
    }),
    useRuntimeAuth: () => ({ hasRole: (role: string) => mocks.roles.includes(role) }),
    MutationDialog: createMutationDialogTestDouble({
      capture: (props) => {
        mocks.dialogProps = props;
      },
      values: { credentialId: "crd_2" },
      submitLabel: "submit",
    }),
  };
});

import {
  CONNECT_WITH_ACCOUNT_FIELD,
  ConnectWithAccountAction,
  type ConnectWithAccountFacts,
  useConnectWithAccountQuery,
} from "./ConnectWithAccountAction";

const SCOPE = "https://www.googleapis.com/auth/calendar.readonly";

const FACTS: ConnectWithAccountFacts = {
  oauth_client: "ocl_google",
  oauth_scopes: [SCOPE],
  accounts: [
    { id: "crd_1", display_name: "ada@example.com", blocked_reason: null },
    { id: "crd_2", display_name: "bob@example.com", blocked_reason: null },
  ],
};

function accountField(): DescriptorField {
  const fields = (mocks.dialogProps?.fields ?? []) as readonly DescriptorField[];
  const field = fields.find((candidate) => candidate.name === CONNECT_WITH_ACCOUNT_FIELD);
  if (!field?.control) throw new Error("The dialog declares no account control.");
  return field;
}

function controlProps(overrides: Partial<DescriptorFieldControlProps> = {}): DescriptorFieldControlProps {
  return {
    id: "account",
    value: undefined,
    readOnly: false,
    describedBy: undefined,
    labelledBy: undefined,
    onChange: vi.fn(),
    dialogValues: {},
    ...overrides,
  };
}

function renderAction(facts: ConnectWithAccountFacts | undefined, connect = vi.fn(async () => ({ id: "src_1" }))) {
  render(
    <ConnectWithAccountAction facts={facts} returnRoute="calendar.sources" label="Connect Google Calendar"
      title="Connect a Google account" connect={connect} />,
  );
  return connect;
}

describe("ConnectWithAccountAction", () => {
  beforeEach(() => {
    mocks.connectAnother.mockClear();
    mocks.dialogProps = null;
    mocks.roles = [];
  });

  test("asks only for the account and connects with the chosen credential", async () => {
    const connect = renderAction(FACTS);
    fireEvent.click(screen.getByRole("button", { name: "Connect Google Calendar" }));

    const fields = (mocks.dialogProps?.fields ?? []) as readonly DescriptorField[];
    expect(fields.map((field) => [field.name, field.required])).toEqual([[CONNECT_WITH_ACCOUNT_FIELD, true]]);
    expect(mocks.dialogProps?.submitLabel).toBe("Connect");

    fireEvent.submit(screen.getByRole("form", { name: "Connect a Google account" }));
    // The dialog runs the authored mutation itself and renders its refusal: no toast repeats it.
    await waitFor(() => expect(connect).toHaveBeenCalledExactlyOnceWith(
      { credentialId: "crd_2" },
      { errorNotification: false },
    ));
  });

  test("offers the user's accounts, marks the chosen one, and connects another with the consumer's scopes", () => {
    renderAction(FACTS);
    const onChange = vi.fn();
    render(accountField().control!(controlProps({ value: "crd_1", onChange })));

    expect(screen.getByRole("button", { name: "ada@example.com" }).getAttribute("aria-current")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "bob@example.com" }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("crd_2");
    expect(mocks.connectAnother).toHaveBeenCalledWith(expect.objectContaining({
      oauthClientId: "ocl_google",
      scopes: [SCOPE],
      next: "/routes/calendar.sources",
      label: "Connect another account",
      disabled: false,
    }));
  });

  test("lists an account the capability refuses with the server's reason, and never chooses it", () => {
    renderAction({ ...FACTS, accounts: [
      { id: "crd_1", display_name: "ada@example.com", blocked_reason: "Already connected" },
      { id: "crd_2", display_name: "bob@example.com", blocked_reason: null },
    ] });
    const onChange = vi.fn();
    render(accountField().control!(controlProps({ onChange })));

    // Listed, with why, but not offered: an unavailable row that keyboard users still reach and
    // hear the reason on, and no "Use this account" verb.
    const refused = screen.getByRole("button", { name: "ada@example.com", description: "Already connected" });
    expect(refused.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(refused);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getAllByTitle("Use this account")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "bob@example.com" }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("crd_2");
  });

  test("a read-only control chooses nothing and connects nothing", () => {
    renderAction(FACTS);
    render(accountField().control!(controlProps({ readOnly: true })));

    expect(screen.queryByRole("button", { name: "ada@example.com" })).toBeNull();
    expect(mocks.connectAnother).toHaveBeenCalledWith(expect.objectContaining({ disabled: true }));
  });

  test("with a ready client and no readable account it says so and offers to connect another", () => {
    renderAction({ oauth_client: "ocl_google", oauth_scopes: [SCOPE], accounts: [] });
    render(accountField().control!(controlProps()));

    expect(screen.getByText("No connected account can read this source yet.")).toBeTruthy();
    expect(screen.queryByText(/isn't configured yet/)).toBeNull();
    expect(mocks.connectAnother).toHaveBeenCalledWith(expect.objectContaining({ oauthClientId: "ocl_google" }));
  });

  test("without a ready client or a readable account it says the connection is not configured", () => {
    renderAction({ oauth_client: null, oauth_scopes: [SCOPE], accounts: [] });
    render(accountField().control!(controlProps()));

    expect(screen.getByText(
      "This connection isn't configured yet. An administrator must set up its OAuth client.",
    )).toBeTruthy();
    expect(screen.queryByText("No connected account can read this source yet.")).toBeNull();
    // Only an administrator may set up an OAuth client: nobody else is sent to the providers page.
    expect(screen.queryByRole("link", { name: "Set up OAuth providers" })).toBeNull();
    expect(mocks.connectAnother).not.toHaveBeenCalled();
  });

  test("an administrator is linked to the OAuth providers page from the unconfigured state", () => {
    mocks.roles = ["angee/role:admin"];
    renderAction({ oauth_client: null, oauth_scopes: [SCOPE], accounts: [] });
    render(accountField().control!(controlProps()));

    expect(screen.getByRole("link", { name: "Set up OAuth providers" }).getAttribute("href"))
      .toBe("/routes/integrate.providers");
  });
});

describe("useConnectWithAccountQuery", () => {
  test("reads the consumer's facts as the user's credentials change, and reloads them once another connected", () => {
    const document = { kind: "Document" } as unknown as Parameters<typeof useConnectWithAccountQuery>[0];
    const { result } = renderHook(() => useConnectWithAccountQuery(document, undefined));

    expect(mocks.query).toHaveBeenCalledWith(document, undefined, { models: ["integrate.Credential"] });
    expect(result.current.data).toEqual({ facts: "loaded" });
    result.current.onConnected();
    expect(mocks.refetch).toHaveBeenCalledOnce();
  });
});
