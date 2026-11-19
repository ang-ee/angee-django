// @vitest-environment happy-dom
import { render, renderHook, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, expect, test, vi } from "vitest";
import { RecordActionBar, parsePageActions, parsePageFields, type FormProps, type RegisteredFormProps } from "@angee/ui";
import { RuntimeFixture, storySchema } from "@angee/storybook/testing";

import { messageForm } from "./MessageForm";
import { RetryMessageDeliveryDocument } from "./documents";

const mutations = vi.hoisted(() => ({ retry: vi.fn(), acknowledge: vi.fn() }));
vi.mock("@angee/ui", async () => ({
  ...await vi.importActual<typeof import("@angee/ui")>("@angee/ui"),
  useResolvedWidget: () => undefined,
  useAuthoredResourceMutation: (document: unknown) => [
    document === RetryMessageDeliveryDocument ? mutations.retry : mutations.acknowledge,
    { fetching: false },
  ],
  useActionResultRun: () => (fire: () => Promise<unknown>) => fire(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mutations.retry.mockResolvedValue({ retry_message_delivery: { ok: true, message: "Queued." } });
  mutations.acknowledge.mockResolvedValue({ acknowledge_message_delivery: { ok: true, message: "Sent." } });
});

test.each([
  { id: "retry-delivery", states: ["FAILED", "UNCERTAIN"], mutation: "retry" as const },
  { id: "mark-as-sent", states: ["UNCERTAIN"], mutation: "acknowledge" as const },
])("$id shows only in its recovery states and submits the observed message revision", async ({ id, states, mutation }) => {
  const { result } = renderHook(() => (messageForm.Component as (props: RegisteredFormProps) => ReactElement<FormProps>)({ resource: "messaging.Message" }));
  const action = parsePageActions(result.current.props.children).find((item) => item.id === id)!;
  expect(action.permission).toBe("send_held");
  expect(action.confirm).toBeTruthy();
  expect(action.primary).toBe(id === "retry-delivery" ? true : undefined);
  expect(parsePageFields(result.current.props.children).some((field) => field.name === "revision")).toBe(true);
  for (const status of ["DRAFT", "QUEUED", "SENT", "RECEIVED", "FAILED", "UNCERTAIN"]) {
    expect(action.visibleWhen?.({ id: "msg_1", revision: 7, status })).toBe(states.includes(status));
    expect(action.visibleWhen?.({ id: "msg_1", revision: 7, status: status.toLowerCase() })).toBe(states.includes(status));
  }
  expect(action.visibleWhen?.({ id: "msg_1", status: states[0] })).toBe(false);
  const refresh = vi.fn();
  await action.run?.({ record: { id: "msg_1", revision: 7, status: states[0], permissions: ["send_held"] },
    values: {}, refresh, update: async () => null, prompt: async () => null });
  expect(mutations[mutation]).toHaveBeenCalledWith({ id: "msg_1", expected_revision: 7 });
  expect(refresh).toHaveBeenCalledOnce();
  await action.run?.({ record: null, values: {}, refresh, update: async () => null, prompt: async () => null });
  expect(mutations[mutation]).toHaveBeenCalledOnce();
});

test("delivery recovery controls are hidden without send_held, even for an uncertain message", () => {
  const { result } = renderHook(() => (messageForm.Component as (props: RegisteredFormProps) => ReactElement<FormProps>)({ resource: "messaging.Message" }));
  render(<RuntimeFixture schemas={storySchema({})}>
    <RecordActionBar fillBand record={{ id: "msg_1", revision: 7, status: "UNCERTAIN", permissions: ["write"] }}
      actions={parsePageActions(result.current.props.children)} />
  </RuntimeFixture>);
  expect(screen.queryByRole("button", { name: "Retry delivery" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Mark as sent" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
});
