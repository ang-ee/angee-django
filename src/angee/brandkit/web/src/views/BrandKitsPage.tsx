import * as React from "react";
import { Column, List, ResourceList, useUiT } from "@angee/ui";

import { useBrandkitT } from "../i18n";
import { BRAND_KIT_MODEL, brandKitForm } from "./BrandKitForm";

/** The deployment's brand kits, the default first; each opens in the registered kit form. */
export function BrandKitsPage(): React.ReactElement {
  const t = useBrandkitT();
  const ui = useUiT();
  return (
    <ResourceList resource={BRAND_KIT_MODEL} form={brandKitForm} placement="inline" routed>
      <List resource={BRAND_KIT_MODEL}>
        <Column field="name" header={t("col.name")} />
        <Column field="is_default" header={t("col.default")} />
        <Column field="brand" header={ui("theme.customization.brand")} />
        <Column field="updated_at" />
      </List>
    </ResourceList>
  );
}
