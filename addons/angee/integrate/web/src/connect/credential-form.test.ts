import { isValidElement, type ReactElement } from "react";
import { describe, expect, test } from "vitest";

import { credentialCreateForm } from "./credential-form";

interface FieldLike {
  name: string;
  widget?: string;
  options?: ReadonlyArray<{ value: string; label: string }>;
  showWhen?: (values: Record<string, unknown>) => boolean;
}

/** The Field props declared by the credential form, keyed by field name. */
function formFields(node: unknown): Map<string, FieldLike> {
  const fragment = node as ReactElement<{ children: ReactElement<FieldLike>[] }>;
  const children = fragment.props.children.filter(isValidElement);
  return new Map(children.map((child) => [child.props.name, child.props]));
}

describe("credentialCreateForm", () => {
  test("offers static-token and ssh-key kinds and swaps the material field", () => {
    const fields = formFields(credentialCreateForm);

    expect([...fields.keys()]).toEqual(["name", "kind", "apiKey", "privateKey"]);
    // The kind discriminator offers only the admin-creatable kinds (OAuth arrives
    // via the connect flow) with the lowercase write values the input expects.
    expect(fields.get("kind")?.options?.map((option) => option.value)).toEqual([
      "static_token",
      "ssh_key",
    ]);

    // The material field follows the selected kind.
    const apiKey = fields.get("apiKey");
    expect(apiKey?.showWhen?.({ kind: "static_token" })).toBe(true);
    expect(apiKey?.showWhen?.({ kind: "ssh_key" })).toBe(false);

    const privateKey = fields.get("privateKey");
    expect(privateKey?.showWhen?.({ kind: "ssh_key" })).toBe(true);
    expect(privateKey?.showWhen?.({ kind: "static_token" })).toBe(false);
  });
});
