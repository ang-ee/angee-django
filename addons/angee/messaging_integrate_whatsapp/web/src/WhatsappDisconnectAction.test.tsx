// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  declared: [] as Record<string, unknown>[],
}));

// Capture what this addon *declares* to integrate's shared button, not what that
// button renders: the confirm gate under test is a declaration, and integrate's
// own package owns testing that the button honors it. Everything else stays the
// real module — `isConnectedOrPaused` included, so the gate asserted below is the
// one integrate exports and cannot drift from it.
vi.mock("@angee/integrate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/integrate")>()),
  ConditionalMutationButton: (props: Record<string, unknown>) => {
    mocks.declared.push(props);
    const when = props.when as (context: { record: Record<string, unknown> }) => boolean;
    return when({ record: { lifecycle: "CONNECTED" } }) ? (
      <button type="button" data-field={String(props.field)} data-variant={String(props.variant)}>
        {props.label as React.ReactNode}
      </button>
    ) : null;
  },
}));

vi.mock("./i18n", () => ({
  useMessagingWhatsappT: () => (key: string) => key,
}));

import { WhatsappDisconnectAction } from "./WhatsappDisconnectAction";

describe("WhatsappDisconnectAction", () => {
  afterEach(() => {
    cleanup();
    mocks.declared.length = 0;
  });

  test("uses the WhatsApp teardown mutation for connected accounts", () => {
    render(<WhatsappDisconnectAction />);

    const button = screen.getByRole("button", { name: "channel.whatsapp.disconnect" });
    expect(button.dataset.field).toBe("disconnect_whatsapp_channel");
    expect(button.dataset.variant).toBe("danger");
  });

  test("keeps a confirm on the destructive verb it specializes", () => {
    // The regression this guards: this entry replaced integrate's *confirmed*
    // Disconnect with a bare `variant="danger"` button while calling a verb that
    // does strictly more — a live session teardown. A specialization must never be
    // the cheaper click of the two. `ConditionalMutationButtonProps` now makes
    // `variant="danger"` without a `confirm` a type error at the owner; this locks
    // the intent so the gate cannot be re-lost behind a widened type.
    render(<WhatsappDisconnectAction />);

    const danger = mocks.declared.filter((props) => props.variant === "danger");
    expect(danger).toHaveLength(1);
    for (const props of danger) {
      const confirm = props.confirm as { title?: unknown; danger?: unknown } | undefined;
      expect(confirm?.title).toBeTruthy();
      expect(confirm?.danger).toBe(true);
    }
  });

  test("gates on the same lifecycle set as the verb it replaces", () => {
    // Reused from integrate rather than re-spelled, so it cannot drift from
    // `Integration.disconnect`'s declared `source=[CONNECTED, PAUSED]`.
    const when = () => mocks.declared[0]?.when as (c: { record: Record<string, unknown> }) => boolean;
    render(<WhatsappDisconnectAction />);

    expect(when()({ record: { lifecycle: "CONNECTED" } })).toBe(true);
    expect(when()({ record: { lifecycle: "PAUSED" } })).toBe(true);
    expect(when()({ record: { lifecycle: "DISCONNECTED" } })).toBe(false);
  });
});
