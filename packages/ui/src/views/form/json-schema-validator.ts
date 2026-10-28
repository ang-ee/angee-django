import Ajv2020 from "ajv/dist/2020.js";
import type { Options } from "ajv";
import addFormats from "ajv-formats";
import { FORM_SPEC_ANNOTATIONS } from "./form-spec.annotations.generated";

/** Draft 2020-12 with native formats, discriminator selection and FormSpec annotations. */
export function createJsonSchemaAjv(options: Options = {}): Ajv2020 {
  const ajv = new Ajv2020({
    strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false,
    ...options, allErrors: true, discriminator: true,
  });
  addFormats(ajv, { mode: "full" });
  for (const keyword of FORM_SPEC_ANNOTATIONS) {
    if (!ajv.getKeyword(keyword)) ajv.addKeyword(keyword);
  }
  return ajv;
}
