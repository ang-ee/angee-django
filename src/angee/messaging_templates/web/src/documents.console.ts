import { graphql } from "@angee/gql/console";

/**
 * A saved row rendered with its sample, in the brand kit it names or the default
 * one, and what it warns about (`MessageTemplate.preview`). `rendered` is null
 * when the row does not render; its one warning then says why.
 */
export const MessageTemplatePreview = graphql(`
  query MessageTemplatePreview($template: ID!, $brand_kit: ID) {
    message_template_preview(template: $template, brand_kit: $brand_kit) {
      rendered { subject preheader html plain_text language }
      warnings { code detail }
    }
  }
`);

/** The brand kits a preview may render in, the default first: the brand kit resource's own order. */
export const MessageTemplatePreviewKits = graphql(`
  query MessageTemplatePreviewKits {
    brand_kits(order_by: [{ is_default: desc }, { name: asc }]) { id name is_default }
  }
`);

/**
 * The rows, in any language, naming one row's kind and key, and whether each
 * renders; null for a reader who may not preview the row. A read for one
 * record's page, never a list's.
 */
export const MessageTemplateBacklinks = graphql(`
  query MessageTemplateBacklinks($id: String!) {
    message_templates_by_pk(id: $id) {
      id
      backlinks { id display_name kind key language is_effective }
    }
  }
`);
