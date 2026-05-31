import type { ReactNode } from "react";

import { PAGE_ELEMENT_SLOT } from "./types";
import type { WidgetOption } from "../../widgets/types";

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
  options?: readonly WidgetOption[];
  placeholder?: string;
  description?: ReactNode;
}

export interface FieldDescriptor {
  name: string;
  label?: ReactNode;
  widget?: string;
  readOnly?: boolean;
  title?: boolean;
  kind?: PageFieldKind;
  options?: readonly WidgetOption[];
  placeholder?: string;
  description?: ReactNode;
}

function FieldMarker(_props: FieldProps): null {
  return null;
}

export const Field = Object.assign(FieldMarker, {
  [PAGE_ELEMENT_SLOT]: "field" as const,
  [FIELD_SLOT]: true,
});
