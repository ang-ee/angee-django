// @vitest-environment happy-dom
import { render, renderHook, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, expect, test, vi } from "vitest";
import { RecordActionBar, parsePageActions, parsePageFields, type FormProps, type RegisteredFormProps } from "@angee/ui";
import { RuntimeFixture, storySchema } from "@angee/storybook/testing";

import { messageForm } from "./MessageForm";

const mutations = vi.hoisted(() => ({ retry: vi.fn(), acknowledge: vi.fn() }));
vi.mock("@angee/ui", async () => ({
  ...await vi.importActual<typeof import("@angee/ui")>("@angee/ui"),
  useResolvedWidget: () => undefined,
  useActionResultMutation: (field: string) => [
    field === "retry_message_delivery" ? mutations.retry : mutations.acknowledge,
    { fetching: false },
  ],
}));

beforeEach(() => {
  vi.clearAllMocks();
  mutations.retry.mockResolvedValue(undefined);
  mutations.acknowledge.mockResolvedValue(undefined);
});

test.each([
  { id: "retry-delivery", verb: "retry", mutation: "retry" as const },
  { id: "mark-as-sent", verb: "acknowledge", mutation: "acknowledge" as const },
])("$id follows projected admission and submits the observed message revision", async ({ id, verb, mutation }) => {
  const { result } = renderHook(() => (messageForm.Component as (props: RegisteredFormProps) => ReactElement<FormProps>)({ resource: "messaging.Message" }));
  const action = parsePageActions(result.current.props.children).find((item) => item.id === id)!;
  expect(action.permission).toBeUndefined();
  expect(action.confirm).toBeTruthy();
  expect(action.primary).toBe(id === "retry-delivery" ? true : undefined);
  expect(parsePageFields(result.current.props.children).some((field) => field.name === "revision")).toBe(true);
  expect(parsePageFields(result.current.props.children).some((field) => field.name === "delivery_actions")).toBe(true);
  expect(action.visibleWhen?.({ id: "msg_1", revision: 7, delivery_actions: [verb] })).toBe(true);
  expect(action.visibleWhen?.({ id: "msg_1", revision: 7, status: "UNCERTAIN", permissions: ["send_held"] })).toBe(false);
  expect(action.visibleWhen?.({ id: "msg_1", revision: 7, delivery_actions: [] })).toBe(false);
  expect(action.visibleWhen?.({ id: "msg_1", delivery_actions: [verb] })).toBe(false);
  const refresh = vi.fn();
  await action.run?.({ record: { id: "msg_1", revision: 7, delivery_actions: [verb] },
    values: {}, refresh, update: async () => null, prompt: async () => null });
  expect(mutations[mutation]).toHaveBeenCalledWith("msg_1", { expected_revision: 7 });
  expect(refresh).toHaveBeenCalledOnce();
  await action.run?.({ record: null, values: {}, refresh, update: async () => null, prompt: async () => null });
  expect(mutations[mutation]).toHaveBeenCalledOnce();
});

test("delivery recovery controls are hidden when the server withholds admission", () => {
  const { result } = renderHook(() => (messageForm.Component as (props: RegisteredFormProps) => ReactElement<FormProps>)({ resource: "messaging.Message" }));
  render(<RuntimeFixture schemas={storySchema({})}>
    <RecordActionBar fillBand record={{ id: "msg_1", revision: 7, status: "UNCERTAIN", delivery_actions: [], permissions: ["send_held"] }}
      actions={parsePageActions(result.current.props.children)} />
  </RuntimeFixture>);
  expect(screen.queryByRole("button", { name: "Retry delivery" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Mark as sent" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
});


test("only uncertainty receives the repeat-send warning", () => {
  const { result } = renderHook(() => (messageForm.Component as (props: RegisteredFormProps) => ReactElement<FormProps>)({ resource: "messaging.Message" }));
  const action = parsePageActions(result.current.props.children).find((item) => item.id === "retry-delivery")!;
  if (typeof action.confirm !== "function") throw new Error("Expected status-specific confirmation");
  expect(action.confirm({ status: "FAILED" }).body).toBe("Retry this failed delivery?");
  expect(action.confirm({ status: "UNCERTAIN" }).body).toContain("send it again");
});
