import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, test } from "vitest";

const hook = fileURLToPath(new URL("../../scripts/pnpm-policy.cjs", import.meta.url));

function configured(config: unknown): unknown {
  return JSON.parse(execFileSync(process.execPath, ["-e", `
    const { hooks } = require(process.argv[1]);
    process.stdout.write(JSON.stringify(hooks.updateConfig(JSON.parse(process.argv[2]))));
  `, hook, JSON.stringify(config)], { encoding: "utf8" }));
}

test("native install policy preserves consumer settings while enforcing framework dependency fixes", () => {
  const config = {
    registry: "https://registry.example.invalid",
    overrides: { "consumer-package": "1.0.0", "@assistant-ui/react": "9.0.0" },
    packageExtensions: {
      "consumer-package": { peerDependencies: { react: "*" } },
      "@refinedev/core@5.0.12": {
        dependencies: { "consumer-helper": "2.0.0", graphql: "15.0.0" },
        peerDependencies: { react: "*" },
      },
    },
    patchedDependencies: { "consumer-package": "consumer.patch", "@refinedev/core@5.0.12": "wrong.patch" },
  };
  expect(configured(config)).toEqual({
    registry: config.registry,
    overrides: {
      "consumer-package": "1.0.0", "@assistant-ui/react": "0.14.26",
      "@assistant-ui/core": "0.2.20", "@assistant-ui/store": "0.2.19", "@assistant-ui/tap": "0.9.3",
    },
    packageExtensions: {
      "consumer-package": { peerDependencies: { react: "*" } },
      "@refinedev/core@5.0.12": {
        dependencies: { "consumer-helper": "2.0.0", graphql: "^16.14.0" },
        peerDependencies: { react: "*" },
      },
    },
    patchedDependencies: {
      "consumer-package": "consumer.patch",
      "@refinedev/core@5.0.12": fileURLToPath(new URL("../../scripts/patches/refinedev-core-5.0.12.patch", import.meta.url)),
    },
  });
});

test("native install policy accepts roots with no existing dependency policy", () => {
  expect(configured({})).toMatchObject({
    overrides: { "@assistant-ui/react": "0.14.26" },
    packageExtensions: { "@refinedev/core@5.0.12": { dependencies: { graphql: "^16.14.0" } } },
    patchedDependencies: { "@refinedev/core@5.0.12": expect.stringContaining("refinedev-core-5.0.12.patch") },
  });
});
