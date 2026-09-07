import type { ReactNode } from "react";

import type { ResourceViewGroup } from "../resource/resource-view-model";
import { PAGE_ELEMENT_SLOT } from "./types";

export interface FacetProps {
  /** Canonical relation axis on the current resource; its query owns option identity and labels. */
  field: string;
  /** Facet title in the toolbar; defaults to the field name. Does not override axis labels. */
  label?: ReactNode;
  /** Related rows fetched for the facet picker. */
  pageSize?: number;
  /** Custom group axis; `false` suppresses group option generation. */
  group?: ResourceViewGroup | false;
}

export interface FacetDescriptor extends FacetProps {}

function FacetMarker(_props: FacetProps): null {
  return null;
}

export const Facet = Object.assign(FacetMarker, {
  [PAGE_ELEMENT_SLOT]: "facet" as const,
});
