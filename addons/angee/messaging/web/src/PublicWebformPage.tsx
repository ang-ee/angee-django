import { PublicLayout, useRouteParam } from "@angee/ui";
import type * as React from "react";

import { PublicWebform } from "./PublicWebform";

export function PublicWebformPage(): React.ReactElement {
  const slug = useRouteParam("slug") ?? "";
  return <PublicLayout><PublicWebform slug={slug} /></PublicLayout>;
}
