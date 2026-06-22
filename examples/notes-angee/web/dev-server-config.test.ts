import { describe, expect, test } from "vitest";

import config from "./vite.config";

describe("notes host Vite config", () => {
  test("binds the dev server to every loopback address family", () => {
    expect(config.server?.host).toBe(true);
    expect(config.server?.strictPort).toBe(true);
  });

  test("proxies Django static files for addon-owned assets", () => {
    expect(config.server?.proxy).toHaveProperty("/static/");
  });
});
