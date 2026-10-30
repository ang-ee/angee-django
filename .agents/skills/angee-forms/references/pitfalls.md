# Form, widget and save pitfalls

Traps moved from the frontend guide's Pitfalls section. The widget and save contracts stay in [Rules](../../../../docs/frontend/guidelines.md#rules), [Form save contracts](../../../../docs/frontend/guidelines.md#form-save-contracts) and the backend's [GraphQL actor and write contracts](../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts); this file adds no rule.

- **An `ID`-scalar to-one is selected as a leaf, not an object.** Any FK a node
  projects as a bare `ID!` rather than a nested object is a scalar on the wire
  ([relation widgets follow the SDL field kind](../../../../docs/frontend/guidelines.md#rules)):
  FormView must select it *without* a sub-selection, or the detail query fails to
  build with a "must not have a selection" GraphQL error. The scalar-id form
  reads/writes the flat id; the object form reads the nested `{id}`.
- **An enum field reads UPPERCASE but writes lowercase** — the backend's
  [status-field contract](../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts):
  a `StateField`/`ImplClassField` column serializes the member name on read
  (`GITHUB`, `ACTIVE`) while its create/patch `String` input takes the lowercase
  value (`github`, `active`). A bare metadata-driven `select` submits the member
  name, which the String input rejects. On a create form pass `options` with
  lower-cased values (the member name is `key.upper()`, so
  `value.toLowerCase()`) and mark the field `createOnly`, so the read-side casing
  never has to round-trip back through the select. To keep the field *editable*
  instead, the `select`/`combobox` widgets reconcile the UPPERCASE read back to the
  authored option via `canonicalOptionValue` (a case-insensitive unique match), so
  lower-cased `options` round-trip correctly without `createOnly`. For status verbs
  prefer an `<Action set={{status:"disabled"}}>` over an editable status field.
  Editable lines need neither: the lines composer lower-cases enum cells and
  omits blank cells at the diff boundary
  ([Form save contracts](../../../../docs/frontend/guidelines.md#form-save-contracts)).
- **A FormView create dialog under the console layout** needs
  `<ControlBandProvider host={undefined}>` to keep its Save band inline instead of
  portaling into the layout's band.
