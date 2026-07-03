import { recordValue } from "./dialect/wire";

export function authoredQueryMeta(
  modelLabels: readonly string[],
): Record<string, unknown> | undefined {
  return modelLabels.length > 0 ? { angeeModels: [...modelLabels] } : undefined;
}

export function authoredQueryReadsAnyModel(
  meta: unknown,
  modelLabels: readonly string[],
): boolean {
  const models = recordValue(meta)?.angeeModels;
  if (!Array.isArray(models)) return false;
  const wanted = new Set(modelLabels);
  return models.some((model) => typeof model === "string" && wanted.has(model));
}
