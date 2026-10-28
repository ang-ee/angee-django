/* Generated from addons/angee/base/schemas/form_spec.schema.json. Do not edit. */

export type FormSpecDocument = FormSpecWire & {
  type?: "object";
  [k: string]: unknown;
};
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "JsonValue".
 */
export type JsonValue =
  | null
  | string
  | number
  | boolean
  | JsonValue[]
  | {
      [k: string]: JsonValue;
    };
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Filter".
 */
export type Filter =
  | {
      operator: "and" | "or";
      key?: string;
      value: Filter[];
    }
  | {
      operator:
        | "eq"
        | "ne"
        | "eqs"
        | "nes"
        | "lt"
        | "gt"
        | "lte"
        | "gte"
        | "in"
        | "nin"
        | "ina"
        | "nina"
        | "contains"
        | "ncontains"
        | "containss"
        | "ncontainss"
        | "between"
        | "nbetween"
        | "null"
        | "nnull"
        | "startswith"
        | "nstartswith"
        | "startswiths"
        | "nstartswiths"
        | "endswith"
        | "nendswith"
        | "endswiths"
        | "nendswiths";
      field: string;
      value?: JsonValue;
    };

/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Field".
 */
export interface FormSpecWire {
  type?:
    | ("string" | "integer" | "number" | "boolean" | "object" | "array" | "any" | "null")
    | ("string" | "integer" | "number" | "boolean" | "object" | "array" | "null")[];
  required?: string[];
  title?: string;
  description?: string;
  readOnly?: boolean;
  nullable?: boolean;
  minimum?: number;
  maximum?: number;
  default?: JsonValue;
  const?: JsonValue;
  format?: string;
  pattern?: string;
  enum?: JsonValue[];
  discriminator?: {
    propertyName: string;
  };
  propertyOrder?: string[];
  widget?: string;
  label?: string;
  addLabel?: string;
  removeLabel?: string;
  placeholder?: string;
  hidden?: boolean;
  layout?: "context" | "input";
  omittable?: boolean;
  presenceRequired?: boolean;
  defaultValue?: JsonValue;
  options?: {
    value: JsonValue;
    label: string;
    disabled?: boolean;
    [k: string]: JsonValue | string | boolean | undefined;
  }[];
  relation?: Relation;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  properties?: {
    [k: string]: FormSpecWire;
  };
  $defs?: {
    [k: string]: FormSpecWire;
  };
  definitions?: {
    [k: string]: FormSpecWire;
  };
  items?: FormSpecWire;
  if?: FormSpecWire;
  then?: FormSpecWire;
  else?: FormSpecWire;
  not?: FormSpecWire;
  oneOf?: FormSpecWire[];
  anyOf?: FormSpecWire[];
  allOf?: FormSpecWire[];
  $ref?: string;
  additionalProperties?: boolean | FormSpecWire;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Relation".
 */
export interface Relation {
  resource: string;
  permission?: string;
  labelField?: string;
  filters?: Filter[];
  create?: {
    resource: string;
    defaultValues?: {
      [k: string]: JsonValue;
    };
    actionLabel?: string;
    title?: string;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Annotations".
 */
export interface Annotations {
  title?: string;
  description?: string;
  readOnly?: boolean;
  propertyOrder?: string[];
  widget?: string;
  label?: string;
  addLabel?: string;
  removeLabel?: string;
  placeholder?: string;
  hidden?: boolean;
  layout?: "context" | "input";
  omittable?: boolean;
  presenceRequired?: boolean;
  defaultValue?: JsonValue;
  options?: {
    value: JsonValue;
    label: string;
    disabled?: boolean;
    [k: string]: JsonValue | string | boolean | undefined;
  }[];
  relation?: Relation;
  [k: string]: unknown;
}
