import * as React from "react";

import {
  FormView,
  type FormViewProps,
} from "./FormView";
import {
  PAGE_ELEMENT_SLOT,
  requirePageResource,
} from "./page";

/**
 * Declarative form view.
 *
 * Used standalone, `Form` renders `FormView` directly. Used as a `ResourceList`
 * child, the element is parsed as a view declaration and `ResourceList` stitches it
 * into the collection-record page. Export and reuse element constants directly;
 * wrapper components hide the marker from the parser.
 */
export interface FormProps
  extends Omit<FormViewProps, "resource" | "fields" | "groups" | "children"> {
  /**
   * Resource rendered by this form, e.g. `"notes.Note"`.
   *
   * Required when rendered standalone. When nested inside `ResourceList`, this may
   * be omitted and is inherited from the page; if both are declared, they must
   * match.
   */
  resource?: string;
  /** Field and group element declarations for this form. */
  children?: React.ReactNode;
}

function FormComponentImpl({
  resource,
  children,
  ...props
}: FormProps): React.ReactElement {
  const resolvedResource = requirePageResource("Form", resource);

  return (
    <FormView
      {...props}
      resource={resolvedResource}
    >
      {children}
    </FormView>
  );
}

/**
 * Render a reusable form declaration standalone, or hand the same element to
 * `ResourceList` for page-level composition. Element constants are the reuse unit;
 * wrapper components hide the marker from the parser.
 */
export const Form = Object.assign(FormComponentImpl, {
  [PAGE_ELEMENT_SLOT]: "form" as const,
});
