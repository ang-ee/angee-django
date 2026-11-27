import { graphql } from "@angee/gql/console";

/**
 * A saved row rendered with its sample, in the brand kit it names or the default
 * one, and what it warns about (`MessageTemplate.preview`). With `patch`, what the
 * open form's Save would send, the row is rendered with those edits as Save would
 * write them, for a caller who may also write it. `rendered` is null when nothing
 * renders, and the warnings then say why: `SAVE_REFUSED` for each refusal Save
 * would answer the edits with, `RENDER_ERROR` for what the sample cannot render.
 * A warning's `field` is the column it belongs to, null for the row as a whole.
 * `direction` is the one the mail's HTML says, for the text shown outside the frame.
 */
export const MessageTemplatePreview = graphql(`
  query MessageTemplatePreview($template: ID!, $brand_kit: ID, $patch: message_templates_set_input) {
    message_template_preview(template: $template, brand_kit: $brand_kit, patch: $patch) {
      rendered { subject preheader html plain_text language direction }
      warnings { code detail field }
    }
  }
`);

/**
 * The rows, in any language, naming one row's kind and key, and whether each is
 * a shipped default the deployment overrides; null for a reader who may not
 * preview the row. A read for one record's page, never a list's.
 */
export const MessageTemplateBacklinks = graphql(`
  query MessageTemplateBacklinks($id: String!) {
    message_templates_by_pk(id: $id) {
      id
      backlinks { id display_name kind key language is_overridden }
    }
  }
`);
