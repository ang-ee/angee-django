import { rowValueAtPath } from "@angee/metadata";
import { acknowledgeFormSubmit, formSubmitError, useAuthoredResourceMutation, type FormSubmit } from "@angee/ui";

import { useKnowledgeT } from "../i18n";
import { KnowledgeUpdatePageBody, PAGE_READ_MODELS } from "./documents";

/** Adapt Knowledge's atomic, hash-guarded body write to the native form contract. */
export function usePageSubmit(): FormSubmit {
  const t = useKnowledgeT();
  const [write] = useAuthoredResourceMutation(KnowledgeUpdatePageBody, {
    invalidateModels: PAGE_READ_MODELS,
    shouldInvalidate: (data) => data?.update_page_body.ok === true,
  });
  return async (data, context) => {
    if (!context.id || !context.baselineRecord) return formSubmitError(t("editor.saveFailed"));
    const hash = rowValueAtPath(context.baselineRecord, "markdown.body_hash");
    if (typeof hash !== "string") return formSubmitError(t("editor.saveFailed"));
    const result = (await write({
      page: context.id,
      ...(typeof data.title === "string" ? { title: data.title.trim() } : {}),
      body: String(context.values.body ?? ""),
      expected_hash: hash,
    }))?.update_page_body;
    if (!result?.ok || !result.markdown) {
      const message = result?.error || t("editor.saveFailed");
      return result?.error_code === "STALE_BODY"
        ? { status: "conflict", message }
        : formSubmitError(message);
    }
    const title = result.markdown.title;
    return acknowledgeFormSubmit(
      { ...context.record, title, markdown: result.markdown },
      { ...context.values, title, body: result.markdown.body },
    );
  };
}
