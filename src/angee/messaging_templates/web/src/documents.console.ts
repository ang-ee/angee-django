import { graphql } from "@angee/gql/console";

/**
 * A saved row rendered with its sample, in the brand kit it names or the default
 * one, and what it warns about (`MessageTemplate.preview`). `rendered` is null
 * when the row does not render; its one warning then says why. Its `direction`
 * is the one the mail's HTML says, for the text shown outside the frame.
 */
export const MessageTemplatePreview = graphql(`
  query MessageTemplatePreview($template: ID!, $brand_kit: ID) {
    message_template_preview(template: $template, brand_kit: $brand_kit) {
      rendered { subject preheader html plain_text language direction }
      warnings { code detail }
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
