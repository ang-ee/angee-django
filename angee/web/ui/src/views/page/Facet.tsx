import type { ReactNode } from "react";
import type {
  ModelRelationFilterMode,
} from "@angee/metadata";

import type { ResourceViewGroup } from "../resource-view-model";
import { PAGE_ELEMENT_SLOT } from "./types";

export interface FacetProps {
  /** Relation field on the current model, e.g. `provider`. */
  field: string;
  /** Toolbar label; defaults to the relation field name. */
  label?: ReactNode;
  /** Filter field accepted by the current model filter input; defaults to resource metadata. */
  filterField?: string;
  /** Filter value shape; defaults to resource metadata, then lookup for explicit fields. */
  filterMode?: ModelRelationFilterMode;
  /** Aggregate bucket key returned by the API; defaults to resource metadata. */
  aggregateKey?: string;
  /** Related-record display field; defaults to the related model representation. */
  labelField?: string;
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
