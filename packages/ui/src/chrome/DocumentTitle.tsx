import { useEffect, useState } from "react";

import { useRuntimeBrand } from "../runtime";
import { useBreadcrumbItems } from "./Breadcrumb";

/** Keep the browser title in sync with the same leaf the console displays. */
export function DocumentTitle(): null {
  const brand = useRuntimeBrand();
  const leaf = useBreadcrumbItems().at(-1)?.label;
  const [initialTitle] = useState(() => document.title);
  const base = brand?.name ?? initialTitle;
  useEffect(() => {
    document.title = leaf && base ? `${leaf} · ${base}` : leaf || base;
  }, [base, leaf]);
  useEffect(() => () => { document.title = initialTitle; }, [initialTitle]);
  return null;
}
