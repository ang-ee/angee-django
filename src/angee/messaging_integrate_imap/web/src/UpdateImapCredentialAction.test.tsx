// @vitest-environment happy-dom
import { render } from "@testing-library/react";
import type { ComponentProps } from "react";
import { expect, test, vi } from "vitest";
import type { ConditionalMutationButton } from "@angee/integrate";

const props = vi.hoisted(() => vi.fn());
vi.mock("@angee/integrate", async (original) => ({
  ...await original<typeof import("@angee/integrate")>(),
  ConditionalMutationButton: (value: ComponentProps<typeof ConditionalMutationButton>) => { props(value); return null; },
}));
import { UpdateImapCredentialAction } from "./UpdateImapCredentialAction";

test("IMAP replacement sends passwords through the shared transient action and password widget", () => {
  render(<UpdateImapCredentialAction />);
  expect(props).toHaveBeenCalledWith(expect.objectContaining({
    field: "update_imap_channel_credential", transient: true,
    args: expect.arrayContaining([
      expect.objectContaining({ name: "password", widget: "password", autoComplete: "new-password" }),
    ]),
  }));
});
