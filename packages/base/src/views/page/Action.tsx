import type { ReactNode } from "react";

import { PAGE_ELEMENT_SLOT } from "./types";

export const ACTION_SLOT = Symbol.for("@angee/base.page.action");

export interface ActionConfirm {
  title: ReactNode;
  body?: ReactNode;
  danger?: boolean;
}

export interface ActionProps {
  id: string;
  label: ReactNode;
  disabled?: boolean;
  danger?: boolean;
  confirm?: ActionConfirm;
  onClick?: () => void;
}

export interface ActionDescriptor {
  id: string;
  label: ReactNode;
  disabled?: boolean;
  danger?: boolean;
  confirm?: ActionConfirm;
  onClick?: () => void;
}

function ActionMarker(_props: ActionProps): null {
  return null;
}

export const Action = Object.assign(ActionMarker, {
  [PAGE_ELEMENT_SLOT]: "action" as const,
  [ACTION_SLOT]: true,
});
