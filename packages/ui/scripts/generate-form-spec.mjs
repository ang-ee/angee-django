import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import standaloneCode from "ajv/dist/standalone/index.js";
import { build } from "esbuild";
import { compile } from "json-schema-to-typescript";

// Repository tooling supplies the canonical schema explicitly. Package builds
// consume committed outputs and never inspect an installed Python addon.
const sourceArgument = process.argv.indexOf("--schema");
if (sourceArgument < 0 || !process.argv[sourceArgument + 1]) {
  throw new Error("Supply --schema <canonical FormSpec JSON Schema path>.");
}
const source = pathToFileURL(resolve(process.argv[sourceArgument + 1]));
const checking = process.argv.includes("--check");
async function emit(name, contents) {
  const target = new URL(name, destination);
  if (checking) {
    if (await readFile(target, "utf8") !== contents) throw new Error(`${name} is stale; run the repository generate:form-spec command.`);
  } else {
    await writeFile(target, contents);
  }
}
const destination = new URL("../src/views/form/", import.meta.url);
const schema = JSON.parse(await readFile(source, "utf8"));
const banner = "/* Generated from src/angee/base/schemas/form_spec.schema.json. Do not edit. */";
const types = await compile(schema, "FormSpecDocument", {
  bannerComment: banner,
  unreachableDefinitions: true,
  style: { singleQuote: false, semi: true },
});

// Ajv owns validation code. Bundle its native Unicode runtime helper so ordinary
// FormSpec consumers import neither CommonJS helpers nor the optional compiler.
const ajv = new Ajv2020({
  strict: true, allErrors: true, verbose: true, discriminator: true,
  code: { source: true, esm: true },
});
const nativeValidator = ajv.compile(schema);
const bundled = await build({
  stdin: {
    contents: standaloneCode(ajv, nativeValidator),
    resolveDir: dirname(fileURLToPath(import.meta.url)),
    sourcefile: "form-spec.validator.generated.js",
    loader: "js",
  },
  bundle: true,
  write: false,
  format: "esm",
  platform: "browser",
  target: "es2022",
  minify: true,
  legalComments: "none",
  sourcemap: false,
});
// Standard annotation keywords remain owned by the native draft vocabulary.
const nativeAnnotations = ajv.getSchema("https://json-schema.org/draft/2020-12/meta/meta-data").schema.properties;
const annotations = Object.keys(schema.$defs.Annotations.properties).filter((keyword) => !Object.hasOwn(nativeAnnotations, keyword));
await emit("form-spec.generated.ts", types);
await emit("form-spec.annotations.generated.ts",
  `${banner}\n/** FormSpec presentation keywords beyond the native JSON Schema vocabulary.\n * @kind null\n * @reason Generated codec vocabulary consumed by the optional schema validator.\n */\nexport const FORM_SPEC_ANNOTATIONS: readonly string[] = ${JSON.stringify(annotations)};\n`);
await emit("form-spec.validator.generated.js", `${banner}\n${bundled.outputFiles[0].text}`);
await emit("form-spec.validator.generated.d.ts", `${banner}
import type { ValidateFunction } from "ajv";
import type { FormSpecWire } from "./form-spec.generated";
declare const validate: ValidateFunction<FormSpecWire>;
export { validate };
export default validate;
`);
