import * as React from "react";

import type { FormViewProps } from "./FormView";
import { useFormOverride } from "../../runtime";
import type { RuntimeFormRegistration } from "../../runtime/contracts";

/** Props supplied by a resource surface to an addon-owned complete form. */
export type RegisteredFormProps = FormViewProps;

/**
 * One addon-owned complete form, reusable by every entry point for its model.
 * Components are deliberately ordinary ComponentType values; wrap memo/forwardRef
 * components in a plain function so runtime validation stays structural and exact.
 */
export interface RegisteredForm extends RuntimeFormRegistration {
  Component: React.ComponentType<RegisteredFormProps>;
}

/** Declare a complete form without creating another runtime registry. */
export function registerForm(
  resource: string,
  Component: React.ComponentType<RegisteredFormProps>,
): RegisteredForm {
  return { resource, Component };
}

/** Resolve a complete form from the existing composed forms registry. */
export function useRegisteredForm(resource: string): RegisteredForm | undefined {
  const value = useFormOverride(resource);
  if (value == null || React.isValidElement(value)) return undefined;
  if (
    typeof value === "object"
    && "resource" in value
    && (value as { resource?: unknown }).resource === resource
    && "Component" in value
    && typeof (value as { Component?: unknown }).Component === "function"
  ) {
    return value as RegisteredForm;
  }
  throw new Error(`Registered form for "${resource}" is not a valid complete form.`);
}

/** Render the complete form registered for a runtime-selected resource. */
export function RegisteredFormView(props: RegisteredFormProps): React.ReactElement {
  const registration = useRegisteredForm(props.resource);
  if (!registration) {
    throw new Error(`Resource "${props.resource}" has no registered complete form.`);
  }
  const Component = registration.Component;
  return React.createElement(Component, props);
}
