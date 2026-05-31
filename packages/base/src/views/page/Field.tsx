import type { ReactNode } from "react";

import { PAGE_ELEMENT_SLOT } from "./types";

export const FIELD_SLOT = Symbol.for("@angee/base.page.field");

export type PageFieldKind =
  | "text"
  | "textarea"
  | "select"
  | "switch"
  | "readonly"
  | "selection"
  | (string & {});

export interface FieldProps {
  name: string;
  label?: ReactNode;
  widget?: string;
  readOnly?: boolean;
  title?: boolean;
  kind?: PageFieldKind;
}

export interface FieldDescriptor {
  name: string;
  label?: ReactNode;
  widget?: string;
  readOnly?: boolean;
  title?: boolean;
  kind?: PageFieldKind;
}

function FieldMarker(_props: FieldProps): null {
  return null;
}

export const Field = Object.assign(FieldMarker, {
  [PAGE_ELEMENT_SLOT]: "field" as const,
  [FIELD_SLOT]: true,
});
