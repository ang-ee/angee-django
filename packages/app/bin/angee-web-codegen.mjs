#!/usr/bin/env node

import { generateWebRuntime } from "@angee/app/web-codegen";

const options = {};
const args = process.argv.slice(2);
for (let index = 0; index < args.length; index += 2) {
  const arg = args[index];
  if (!arg.startsWith("--")) throw new Error(`Unexpected argument: ${arg}`);
  const value = args[index + 1];
  if (!value || value.startsWith("--")) throw new Error(`Missing value for ${arg}`);
  options[arg.slice(2)] = value;
}
await generateWebRuntime({
  webRoot: options["web-root"] ?? process.cwd(),
  // Preserve the explicit CLI's cwd-relative defaults.
  runtimeDir: options.runtime ?? "../runtime",
});
