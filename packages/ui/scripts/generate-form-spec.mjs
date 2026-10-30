import { readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import standaloneCode from "ajv/dist/standalone/index.js";
import { build } from "esbuild";
import { compile } from "json-schema-to-typescript";

const source = new URL("../../../src/angee/base/schemas/form_spec.schema.json", import.meta.url);
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
await writeFile(new URL("form-spec.generated.ts", destination), types);
await writeFile(new URL("form-spec.annotations.generated.ts", destination),
  `${banner}\n/** FormSpec presentation keywords beyond the native JSON Schema vocabulary.\n * @kind null\n * @reason Generated codec vocabulary consumed by the optional schema validator.\n */\nexport const FORM_SPEC_ANNOTATIONS: readonly string[] = ${JSON.stringify(annotations)};\n`);
await writeFile(new URL("form-spec.validator.generated.js", destination), `${banner}\n${bundled.outputFiles[0].text}`);
await writeFile(new URL("form-spec.validator.generated.d.ts", destination), `${banner}
import type { ValidateFunction } from "ajv";
import type { FormSpecWire } from "./form-spec.generated";
declare const validate: ValidateFunction<FormSpecWire>;
export { validate };
export default validate;
`);
