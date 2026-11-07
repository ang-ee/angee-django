import { useSetAuthoredQueryData } from "@angee/refine";
import { rowValueAtPath } from "@angee/metadata";
import { acknowledgeFormSubmit, formSubmitError, useAuthoredResourceMutation, type FormSubmit } from "@angee/ui";

import { useKnowledgeT } from "../i18n";
import { KnowledgePage, KnowledgeUpdatePageBody, PAGE_READ_MODELS } from "./documents";

/** Adapt Knowledge's atomic, hash-guarded body write to the native form contract. */
export function usePageSubmit(): FormSubmit {
  const t = useKnowledgeT();
  const acceptQuery = useSetAuthoredQueryData();
  const [write] = useAuthoredResourceMutation(KnowledgeUpdatePageBody, {
    invalidateModels: PAGE_READ_MODELS,
    shouldInvalidate: (data) => data?.update_page_body.ok === true,
    acceptResponse: async (data, variables) => {
      const result = data?.update_page_body;
      if (!result?.ok || !result.markdown) return;
      const accepted = result.markdown;
      await acceptQuery(KnowledgePage, { id: String(variables.page) }, (current) => {
        const page = current?.pages_by_pk;
        if (!page || !page.markdown) return current;
        return { ...current, pages_by_pk: { ...page, title: accepted.title, revision: accepted.revision,
          markdown: { ...page.markdown, body: accepted.body, body_hash: accepted.body_hash, word_count: accepted.word_count } } };
      });
    },
  });
  return async (data, context) => {
    if (!context.id || !context.baselineRecord) return formSubmitError(t("editor.saveFailed"));
    const hash = rowValueAtPath(context.baselineRecord, "markdown.body_hash");
    const revision = context.baselineRecord.revision;
    if (typeof hash !== "string" || typeof revision !== "number") return formSubmitError(t("editor.saveFailed"));
    const result = (await write({
      page: context.id,
      ...(typeof data.title === "string" ? { title: data.title.trim() } : {}),
      body: String(context.values.body ?? ""),
      expected_hash: hash,
      expected_revision: revision,
    }))?.update_page_body;
    if (!result?.ok || !result.markdown) {
      const message = result?.error || t("editor.saveFailed");
      return (result?.error_code === "STALE_BODY" || result?.error_code === "STALE_REVISION")
        ? { status: "conflict", message }
        : formSubmitError(message);
    }
    const title = result.markdown.title;
    return acknowledgeFormSubmit(
      { ...context.record, title, revision: result.markdown.revision, body: result.markdown.body, markdown: result.markdown },
      { ...context.values, title, body: result.markdown.body },
    );
  };
}
