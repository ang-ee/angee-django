import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import { publicAddress as ownedAddress } from "./address";
import webforms, { publicAddress } from "./index";

describe("angee.webforms addon manifest", () => {
  test("exports the one owner of a form's public address, for consumers that link to a form", () => {
    expect(publicAddress).toBe(ownedAddress);
  });

  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(webforms)).not.toThrow();
  });

  test("routes forms, versions and responses, each claiming its resource, under one menu root", () => {
    expect(webforms.routes?.flatMap((route) => route.resource ? [[route.name, route.path, route.resource]] : [])).toEqual([
      ["webforms.forms", "/webforms", "webforms.Form"],
      ["webforms.versions", "/webforms/versions", "webforms.FormVersion"],
      ["webforms.responses", "/webforms/responses", "webforms.Response"],
    ]);
    const nodes = Object.entries(webforms.menus ?? {});
    expect(nodes.filter(([, node]) => node.parent === undefined).map(([id]) => id)).toEqual(["webforms"]);
    expect(nodes.filter(([, node]) => node.parent === "webforms").map(([, node]) => node.route))
      .toEqual(["webforms.forms", "webforms.versions", "webforms.responses"]);
  });

  test("registers the form's complete form by model label", () => {
    expect(Object.keys(webforms.forms ?? {})).toEqual(["webforms.Form"]);
  });
});
