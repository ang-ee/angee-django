import * as React from "react";
import { Column, DrawerResourceList, Field, Form, List, NavLink, type RecordPanelContext } from "@angee/ui";

import { publicAddress } from "../address";
import { useWebformsT } from "../i18n";
import { ALIAS_MODEL } from "../resources";

/**
 * A form's public addresses, on the form's own record. Each is a slug the public
 * reaches the form at, optionally in one language; the list links to the address
 * itself. Addresses are added and removed here, and a form without one has no
 * public page.
 */
export function FormAliases({ recordId, form }: Pick<RecordPanelContext, "recordId" | "form">): React.ReactElement {
  const t = useWebformsT();
  return (
    <DrawerResourceList
      resource={ALIAS_MODEL}
      presentation="embedded"
      baseFilter={{ form: { exact: recordId } }}
      createDefaults={{ form: recordId }}
      hideCreate={form.formReadOnly}
    >
      <List resource={ALIAS_MODEL} order={{ slug: "ASC" }} emptyContent={t("alias.empty")}>
        {/* The public page is served beside the app, not by its router: the link opens it as a document. */}
        <Column field="slug" header={t("alias.address")} interactive
          render={(row) => typeof row.slug === "string"
            ? <NavLink href={publicAddress(row.slug)} target="_blank" rel="external noopener" variant="inline">{publicAddress(row.slug)}</NavLink>
            : null} />
        <Column field="language" />
        <Column field="created_at" />
      </List>
      <Form resource={ALIAS_MODEL} readOnly={form.formReadOnly}>
        <Field name="form" readOnly />
        <Field name="slug" title description={t("alias.slugHint")} />
        <Field name="language" description={t("alias.languageHint")} />
      </Form>
    </DrawerResourceList>
  );
}
