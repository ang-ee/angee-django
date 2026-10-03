import { describe, expect, test } from "vitest";

import { DEPLOYMENT_LAYER_ID, resolveShell, type ShellLayer } from "./shell";

const pm: ShellLayer = {
  id: "pm",
  shell: { home: "projects.my-work", brand: { name: "Angee PM", mark: "pm" }, perspective: "pm" },
  perspectives: { pm: { root: "pm" } },
};
const product: ShellLayer = {
  id: "product",
  dependsOn: ["pm"],
  shell: { home: "product.home", perspective: "product" },
  perspectives: { product: { root: "product" } },
};
const deployment = (shell: ShellLayer["shell"]): ShellLayer => ({
  id: DEPLOYMENT_LAYER_ID, dependsOn: ["pm", "product"], shell,
});

describe("resolveShell", () => {
  test("a dependent overrides its dependency field by field", () => {
    const shell = resolveShell([pm, product]);
    expect(shell.home).toBe("product.home");
    expect(shell.brand).toEqual(pm.shell!.brand);
    expect(shell.perspective).toEqual({ id: "product", root: "product" });
    expect(shell.provenance).toEqual({ home: "product", brand: "pm", perspective: "product" });
    expect(shell.diagnostics).toEqual([]);
  });

  test("removing the dependent leaves the bundle's shell", () => {
    expect(resolveShell([pm])).toMatchObject({ home: "projects.my-work", perspective: { id: "pm", root: "pm" } });
  });

  test("the deployment applies last; null keeps the full console", () => {
    const shell = resolveShell([pm, product, deployment({ perspective: null })]);
    expect(shell.perspective).toBeNull();
    expect(shell.home).toBe("product.home");
    expect(shell.provenance.perspective).toBe(DEPLOYMENT_LAYER_ID);
  });

  test("a selected perspective's home wins over the product home, a deployment home over both", () => {
    const withHome: ShellLayer = { ...pm, perspectives: { pm: { root: "pm", home: "pm.home" } } };
    expect(resolveShell([withHome, product, deployment({ perspective: "pm" })]).home).toBe("pm.home");
    expect(resolveShell([withHome, product, deployment({ perspective: "pm", home: "x.home" })]).home).toBe("x.home");
  });

  test("unrelated products fall back to the framework default, unless the deployment pins", () => {
    const nexus: ShellLayer = { id: "nexus", shell: { home: "nexus.inbox" } };
    const unpinned = resolveShell([pm, nexus]);
    expect(unpinned).toMatchObject({ brand: null, perspective: null, provenance: {} });
    expect(unpinned.home).toBeUndefined();
    expect(unpinned.diagnostics).toEqual(["Unrelated products pm, nexus declare a shell; pin one in ANGEE_UI."]);
    const pinned = resolveShell([pm, nexus, { id: DEPLOYMENT_LAYER_ID, dependsOn: ["pm", "nexus"], shell: { perspective: "pm" } }]);
    expect(pinned.perspective).toEqual({ id: "pm", root: "pm" });
  });

  test("unrelated ancestors setting one field leave it to the product", () => {
    const left: ShellLayer = { id: "left", shell: { home: "left.home" } };
    const right: ShellLayer = { id: "right", shell: { home: "right.home" } };
    const top: ShellLayer = { id: "top", dependsOn: ["left", "right"], shell: { brand: { name: "Top", mark: "top" } } };
    const shell = resolveShell([left, right, top]);
    expect(shell.home).toBeUndefined();
    expect(shell.brand?.name).toBe("Top");
    expect(shell.diagnostics).toEqual(["Unrelated layers left, right set shell.home; top or ANGEE_UI decides."]);
  });

  test("fails on an unknown perspective, a duplicate perspective id or an unknown dependency", () => {
    expect(() => resolveShell([{ id: "a", shell: { perspective: "missing" } }])).toThrow(/unknown perspective "missing"/);
    expect(() => resolveShell([pm, { id: "b", perspectives: { pm: { root: "b" } } }])).toThrow(/redefines perspective "pm"/);
    expect(() => resolveShell([{ id: "c", dependsOn: ["ghost"] }])).toThrow(/unknown addon "ghost"/);
  });

  test("the deprecated top-level brand is the layer's shell brand", () => {
    expect(resolveShell([{ id: "a", brand: { name: "A", mark: "a" } }]).brand).toEqual({ name: "A", mark: "a" });
    expect(() => resolveShell([{ id: "a", brand: { name: "A", mark: "a" }, shell: { brand: { name: "B", mark: "b" } } }]))
      .toThrow(/both brand and shell.brand/);
  });
});
