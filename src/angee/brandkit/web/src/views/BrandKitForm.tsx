import * as React from "react";
import { Field, Form, Group, registerForm, useUiT, type RegisteredFormProps } from "@angee/ui";

import { CONTRAST_WARNINGS_SELECTION } from "../ContrastWarnings";
import { useBrandkitT } from "../i18n";

export const BRAND_KIT_MODEL = "brandkit.BrandKit";

/**
 * The brand kit form: the name as its title; the default flag and the logos; the
 * colours in the theme customisation editor's sections and words, since each is
 * that editor's input of the same name; a saved kit's contrast warnings; the
 * fonts, the mail footer and the voice.
 */
function BrandKitForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useBrandkitT();
  const ui = useUiT();
  return (
    <Form {...props} resource={BRAND_KIT_MODEL}>
      <Field name="name" title />
      <Group label={t("form.kit")} columns={3}>
        <Field name="is_default" label={t("field.isDefault")} />
        <Field name="logo" label={t("field.logo")} />
        <Field name="logo_dark" label={t("field.logoDark")} />
      </Group>
      <Group label={ui("theme.customization.section.identity")} hint={t("form.colorsHint")} columns={3}>
        <Field name="brand" label={ui("theme.customization.brand")} />
        <Field name="accent" label={ui("theme.customization.accent")} />
        <Field name="neutral" label={ui("theme.customization.neutral")} />
      </Group>
      <Group label={ui("theme.customization.section.surfaces")} columns={3}>
        <Field name="canvas" label={ui("theme.customization.canvas")} />
        <Field name="surface" label={ui("theme.customization.surface")} />
        <Field name="rail" label={ui("theme.customization.rail")} />
      </Group>
      <Group label={ui("theme.customization.section.feedback")} columns={4}>
        <Field name="success" label={ui("theme.customization.success")} />
        <Field name="warning" label={ui("theme.customization.warning")} />
        <Field name="danger" label={ui("theme.customization.danger")} />
        <Field name="info" label={ui("theme.customization.info")} />
      </Group>
      <Group label={t("form.contrast")} hint={t("form.contrastHint")} savedOnly>
        <Field
          name="contrast_warnings"
          label={t("field.contrastWarnings")}
          selectionPaths={CONTRAST_WARNINGS_SELECTION}
        />
      </Group>
      <Group label={t("form.fonts")} hint={t("form.fontsHint")} columns={2}>
        <Field name="font_heading" label={t("field.fontHeading")} />
        <Field name="font_body" label={t("field.fontBody")} />
      </Group>
      <Group label={t("form.footer")} columns={2}>
        <Field name="footer_organization" label={t("field.footerOrganization")} />
        <Field name="footer_address" label={t("field.footerAddress")} widget="textarea" />
      </Group>
      <Group label={t("form.voice")} hint={t("form.voiceHint")}>
        <Field name="voice" label={t("form.voice")} widget="textarea" />
      </Group>
    </Form>
  );
}

export const brandKitForm = registerForm(BRAND_KIT_MODEL, BrandKitForm);
