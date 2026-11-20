import { expect, test } from "vitest";

import manifest from "../package.json";
import { publicAddress } from "./address";

test("a form's public address is its slug under the path the standalone page declares", () => {
  const [path] = manifest.angee.pages["webforms.form"].paths;
  expect(path).toMatch(/^\/.+\/\*$/);
  expect(publicAddress("semilla")).toBe(`${path?.slice(0, -1)}semilla`);
});
