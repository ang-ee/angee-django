import * as React from "react";

import {
  FormView,
  type FormViewProps,
} from "./FormView";
import {
  PAGE_ELEMENT_SLOT,
  requirePageModel,
} from "./page";

/**
 * Declarative form view.
 *
 * Used standalone, `Form` renders `FormView` directly. Used as a `DataPage`
 * child, the element is parsed as a view declaration and `DataPage` stitches it
 * into the collection-record page. Export and reuse element constants directly;
 * wrapper components hide the marker from the parser.
 */
export interface FormProps
  extends Omit<FormViewProps, "model" | "fields" | "groups" | "children"> {
  /**
   * Model label rendered by this form, e.g. `"notes.Note"`.
   *
   * Required when rendered standalone. When nested inside `DataPage`, this may
   * be omitted and is inherited from the page; if both are declared, they must
   * match.
   */
  model?: string;
  /** Field and group element declarations for this form. */
  children?: React.ReactNode;
}

function FormComponentImpl({
  model,
  children,
  ...props
}: FormProps): React.ReactElement {
  const resolvedModel = requirePageModel("Form", model);

  return (
    <FormView
      {...props}
      model={resolvedModel}
    >
      {children}
    </FormView>
  );
}

/**
 * Render a reusable form declaration standalone, or hand the same element to
 * `DataPage` for page-level composition. Element constants are the reuse unit;
 * wrapper components hide the marker from the parser.
 */
export const Form = Object.assign(FormComponentImpl, {
  [PAGE_ELEMENT_SLOT]: "form" as const,
});
