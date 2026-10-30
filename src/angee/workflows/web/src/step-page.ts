import type { UiTranslate } from "@angee/ui";

/** Reader-facing pages are one-based; retained execution indices stay zero-based. */
export function formatStepPage(index: unknown, t: UiTranslate): string {
  return typeof index === "number" ? t("step.pageNumber", { number: index + 1 }) : "";
}

/** Native read-only fields adapt presentation without changing retained values. */
export function stepPageCodec(t: UiTranslate) {
  return { toControl: (value: unknown) => formatStepPage(value, t), fromControl: (value: unknown) => value };
}
