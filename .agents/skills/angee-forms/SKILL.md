---
name: angee-forms
description: "Builds forms and widgets in an Angee addon: registered JSX forms (registerForm, Form/Field/Group), FormSpec forms from a Pydantic config_model, JSON-schema action dialogs, field widgets, relation pickers, save/error/revision contracts. Use for 'add a form', 'new widget', 'form spec'."
---

# Angee Forms

## Use when

- "Add a form for `<app>.<Model>`" / "the picker's Create should open our form".
- "Add a `money`/`color`/custom widget" / "render field X with its own control".
- "Expose this implementation's `config_model` as a settings form" (FormSpec).
- "Run a verb with inputs from a JSON Schema" (workflow input, decision answer).
- "Field errors show as one banner" / "stale edit overwrote" / "save did not refresh".

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `src/angee/base/schemas/forms.py` | FormSpec projection of a Pydantic JSON Schema; annotation checks | `config_form_spec` :262, `check_form_annotations` :52 |
| `src/angee/base/schemas/form_spec.schema.json` | The one FormSpec contract (Python validator, generated TS types, standalone Ajv) | `$defs.Annotations`, `$defs.Relation` |
| `src/angee/base/implementations.py` | `config_model` → `ImplChoice.config_schema` | `config_model` :46, `config_schema` :28, `choice()` :110 |
| `src/angee/graphql/data/resource_fields.py` | Widget and currency classification from field declarations | `resource_field_widget` :118, `is_resource_field_widget` :54 |
| `src/angee/graphql/schema.py` | Django `ValidationError` → `VALIDATION` extension with per-field messages | `_apply_validation_error` :200 |
| `packages/ui/src/views/form/registered-form.ts` | Complete-form registration and lookup | `registerForm` :22, `RegisteredFormView` :50 |
| `packages/ui/src/views/form/FormView.tsx`, `use-form-view-save.ts` | The form surface; native save, its invalidation and `expected_revision`; the custom-submit branch | `useFormViewSave` :187, revision detection :216-217, custom branch :475, native `expected_revision` :486-490 |
| `packages/ui/src/views/form/validation-errors.ts` | `FormSubmitResult`; error decoding; React Hook Form binding | :27, `formSubmitError` :73, `applyFormErrors` :97 |
| `packages/ui/src/views/form/form-spec*.ts`, `json-schema.ts` | FormSpec parse/deserialize; Ajv resolver; schema action args | `parseFormSpec` :13, `deserializeFormSpec` :78, `jsonSchemaActionArgs` :146 |
| `packages/ui/src/widgets/types.ts`, `packages/metadata/src/fields.ts` | Widget contract; default widget per field metadata | `WidgetDefinition` :206, `defaultWidgetForModelField` :62 |

## Reuse first

- `Form`, `Field`, `Group` (`packages/ui/src/views/page/`): read `FieldDescriptor`
  (Field.tsx:18) and `GroupProps` (Group.tsx:9) before any control or branch.
- `ResourceList form={…}`, `RegisteredFormView` and `useRelationForms`
  (`views/relation/RelationRecordDialog.tsx:39`) all resolve the one registered form.
- The built-in widget registry (`packages/ui/src/widgets/index.ts`) before any new control.
- Relation owners: `relationFieldInfo`/`relationListFieldInfo` (`views/resource/model-metadata-defaults.ts:61,114`),
  `RelationFieldWidget`, `RelationMultiFieldWidget`, `views/relation/relation-options.ts`.
- Dialog and result owners: `MutationDialog` + `DescriptorFieldList`; `<Action args submit>` via `RecordActionBar`;
  `savedFormSubmitResult`, `formSubmitError`, `actionOutcomeSubmitResult`, `applyFormErrors`, `createJsonSchemaResolver`.
- Backend: `ImplBase.config_model`, `config_form_spec`, `register_field_type` (`angee.graphql.field_types`),
  `MONEY_CURRENCY_FIELD_METADATA_KEY`. Controls: `Input`, `Combobox`, `FieldRoot`/`FieldLabel`, `widgetLabel`,
  `formatNumber`, `date-format.ts`; every control extends the `widget-control` surface.

## Steps

1. **Choose the path.** Form edits one resource's records → registered form (3).
   Page needs a caller-specific field set → inline `<Form>` under `ResourceList` (4),
   never a second registration. Typed configuration (`ImplBase.config_model`) → FormSpec (6).
   A verb takes a JSON Schema → schema action dialog (7). A control the registry lacks → widget (5).
   **Owner rule:** pages stay thin; form mechanics belong to `@angee/ui` ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Routes and pages stay thin").

2. **Declare the backend facts on the field class.** Metadata projects them as
   `widget` and `currencyField` (`src/angee/money/fields.py`).

   ```python
   class MoneyField(models.DecimalField):
       angee_widget = "money"                          # the key the web addon registers
       def __init__(self, *args, currency_field: str = "currency", **kwargs):
           self.currency_field = currency_field         # semantic; never in migration state
           kwargs.setdefault("max_digits", 18)
           kwargs.setdefault("decimal_places", 6)
           super().__init__(*args, **kwargs)
       @property
       def angee_currency_field(self) -> str:          # → metadata `currencyField`
           return self.currency_field
   register_field_type(MoneyField, decimal.Decimal)   # the GraphQL scalar for the column
   ```

   On a computed GraphQL field → `strawberry_django.field(metadata={"angee_widget": …})`. An
   addon-specific key follows [custom resource widget keys](../../../docs/frontend/guidelines.md#rules)
   (`angee.tags.recordTags`). **Owner rule:** field classes own classification and
   `resource_field_widget` special-cases no addon class ([backend rules](../../../docs/backend/guidelines.md#rules), "Field classes own…").

3. **Register the complete form** (`src/angee/tags/web/src/views/TagForm.tsx`),
   in an eager module; the routed page stays lazy.

   ```tsx
   function TagForm({ resource: _resource, ...props }: RegisteredFormProps) {
     const t = useTagsT();
     const scopeFieldEntries = useContainer("tags.tags#fields");   // other addons add fields
     return (
       <Form {...props} resource={TAG_MODEL}>
         <Field name="name" title />
         <Group label={t("form.details")} columns={2}>
           <Field name="color" label={t("col.color")} />
           <Field name="attention" label={t("col.attention")} />
           {containerContents(scopeFieldEntries)}
         </Group>
         <Group savedOnly><Field name="is_archived" /></Group>
       </Form>
     );
   }
   export const tagForm = registerForm(TAG_MODEL, TagForm);
   ```

   `index.tsx` contributes `forms: { "tags.Tag": tagForm }` and the `"tags.tags#fields": {}`
   container; `TagsPage.tsx` passes `<ResourceList resource={TAG_MODEL} form={tagForm} placement="inline" routed>`.
   The component owns create/edit through `props.id`. **Owner rule:** one registration per
   canonical model label; routes and pickers resolve it through `useFormOverride` (`packages/ui/src/runtime/runtime.ts:369`)
   ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Register a resource's complete form once").

4. **Declare fields for a caller-specific form** (`examples/src/example/notes/web/src/NotePage.tsx`).

   ```tsx
   <Form resource={MODEL}>
     <Field name="title" widget="text" title />
     <Field name="status" widget="statusbar" status />
     <Group label={t("form.details")} columns={2}>
       <Field name="reminder_at" label={t("form.reminder")} widget="datetime" />
       <Field name="tags" widget="tagInput" />
     </Group>
     <Field name="body" widget="markdown.editor" />
   </Form>
   ```

   Omit `widget` for the metadata default (`defaultWidgetForModelField`, `packages/metadata/src/fields.ts:62`).
   Narrow, filter, branch and split create/edit through `FieldDescriptor` props (`relationResource`,
   `filters`, `create={null}`, `showWhen`, `createOnly`/`editOnly`; Field.tsx:18).
   **Owner rule:** the descriptor resolves its own widget (`fieldWidgetId`,
   Field.tsx:103); a view never re-derives it ([owning object](../../../docs/guidelines.md#put-behavior-on-the-owning-object)).

5. **Build a widget** (`src/angee/money/web/src/widgets/money.tsx`). The value is
   the wire scalar (a Decimal string); `read` is required, `edit`/`cell` optional.

   ```tsx
   function MoneyRead({ value, row, field }: WidgetRenderProps<MoneyWidgetValue>) {
     const code = resolveCurrencyCode(row, field?.currencyField);   // metadata path, read off the row
     return <span className={textRoleVariants({ role: "value" })}>{formatMoney(value, code)}</span>;
   }
   function MoneyEdit({ value, onChange, field, readOnly, controlRef }: WidgetRenderProps<MoneyWidgetValue>) {
     return (
       <Input {...field?.controlProps} ref={controlRef} value={value ?? ""} readOnly={readOnly}
         inputMode="decimal" aria-label={widgetLabel(field, "Amount")}
         onChange={(event) => onChange?.(event.currentTarget.value)} />
     );
   }
   export const moneyWidget = { edit: MoneyEdit, read: MoneyRead, cell: MoneyRead }
     satisfies WidgetDefinition<MoneyWidgetValue>;
   // index.tsx
   const money = defineBaseAddon({ id: "money", widgets: { money: moneyWidget } });
   ```

   Spread `field.controlProps` so descriptor forms keep ids and aria wiring; `acceptsRowTemplate: true`
   only for a fixed-row widget. Test beside it (`money.test.tsx`) with `createElement(moneyWidget.read, {…})`.
   **Owner rule:** backend key and registered key are one name, contributed at build time; the widget
   reads `field` and `row`, never a model probe ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Custom resource widget keys").

6. **Project a typed config into a FormSpec form.** Annotate the Pydantic model
   (`src/angee/workflows_agents/steps.py:29` shape); the base owner projects it.

   ```python
   class WebhookConfig(BaseModel):
       url: str = Field(title="Endpoint", description="Where deliveries are posted.")
       agent: str = Field(min_length=1, json_schema_extra={"relation": {"resource": "agents.Agent"}})
       options: dict[str, object] = Field(default_factory=dict, json_schema_extra={"widget": "json"})

   class WebhookBackend(ImplBase):
       config_model = WebhookConfig
       config_form_spec_json_fields = False   # True renders unsupported shapes through the json widget
   ```

   The supported shapes and annotation keys are the owner's contract: `config_form_spec` (forms.py:262)
   and `$defs.Annotations` in `form_spec.schema.json`; an unsupported shape fails unless
   `config_form_spec_json_fields`. The UI resolves the spec with `useFormSpecFields` (form-spec.ts:89)
   and `formSpecInitialValues` (:135).
   **Owner rule:** `parse_config` stays the validation owner; FormSpec is a projection, never a second
   schema ([backend rules](../../../docs/backend/guidelines.md#rules), "Pydantic declarations own implementation config defaults").

7. **Open a schema action dialog** (`src/angee/workflows/web/src/run-workflow-actions.tsx`).

   ```tsx
   import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";   // Ajv lives only here
   actions.push({ id: workflow.id, label: workflow.name,
     args: () => jsonSchemaActionArgs(published.input_schema, widgets),
     submit: async (values) => start(workflow.id, { input: values }) });   // resolves the ActionOutcome
   <RecordActionBar record={chrome.record} actions={actions} />
   ```

   A `oneOf` schema needs a declared `discriminator`. **Owner rule:** `DescriptorFieldList` owns branch
   reset, FormSpec owns values and widgets, Ajv validates only the selected branch ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Schema-driven forms").

8. **Save, errors, revision.** Native saves need nothing; add `invalidateModels` when another
   read model changes ([form save contracts](../../../docs/frontend/guidelines.md#form-save-contracts)).
   A custom `submit` returns `savedFormSubmitResult` or `formSubmitError(error)` ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Submit owners return FormSubmitResult").
   On the backend raise Django `ValidationError({"field": "…"})` from `clean()` or the manager; the wire
   mapping is `_apply_validation_error` (`src/angee/graphql/schema.py:200`) and `applyFormErrors` binds it. For stale-edit
   protection compose `OptimisticLockMixin` (`base/models/locks.py:189`); the token contract is
   [write contracts](../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts) and `useFormViewSave` sends it on native saves only (:216-217, :486-490).
   **Owner rule:** `FormView` owns native saves, their invalidation and `expected_revision` (`use-form-view-save.ts:486-490`); a custom `submit` keeps its mutation owner's invalidation and revision handling (`:475`; [form save contracts](../../../docs/frontend/guidelines.md#form-save-contracts) "custom commands retain their mutation owner's invalidation"); a page adds neither.

## Do not

- Hand-roll a form, detail or table in a page ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Use shared page, resource, form…").
- Register a model's form twice or treat inline `fields` as a registration ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Register a resource's complete form once").
- Add a widget registry or register at runtime; `widgets:`/`forms:` compose at build time ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Compose addon capabilities at build time").
- Use a widget key the field never declared, or a custom key outside `namespace.addon.widgetName` ([custom resource widget keys](../../../docs/frontend/guidelines.md#rules); `is_resource_field_widget`, resource_fields.py:54).
- Add another in-band form transport or hand-written `…Data`/`…Variables` types ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Submit owners return FormSubmitResult").
- Decide permissions or business state in a form; `visibleWhen` reads projected admission ([keep policy above detail](../../../docs/guidelines.md#keep-policy-above-detail)).
- Mirror form values, baselines or errors into local state beside React Hook Form ([frontend rules](../../../docs/frontend/guidelines.md#rules), "React state has one owner").
- Re-uppercase enum options or add casing workarounds ([frontend rules](../../../docs/frontend/guidelines.md#rules), "Enum casing is decided once").
- Maintain a second JSON Schema or validator beside `form_spec.schema.json` ([stack](../../../docs/stack.md#frontend), ajv row; [backend rules](../../../docs/backend/guidelines.md#rules), "JSON Schema concerns have explicit owners").
- Hand-roll a relation picker or chips control ([frontend rules](../../../docs/frontend/guidelines.md#rules), "A to-many relation is a relation multi-select").

## Verify

Done means: the manifest test lists the key (`Object.keys(addon.forms)`/`addon.widgets`, as
`src/angee/tags/web/src/index.test.ts` does); `check` and `schema --check` accept the widget key;
emitted metadata carries `widget` (and `currencyField`); in the browser the form saves, a stale edit
reports a conflict, and a `ValidationError` lands under its field.

- Lane (jj workspace: no host, database or JS install): focused pytest on the field class and projection
  (`tests/test_resource_widgets.py`, `tests/test_impl.py` shapes), ruff and mypy on touched files, layering
  tests — [Python contracts](../../../docs/checks.md#python-and-composition-contracts).
- Integrated `src` only: `check`, `schema --check`, codegen, the addon package's vitest and typecheck, the
  composed `web` typecheck, the guardrail test (raw `<input>`/`<form>` fail there) — [composition](../../../docs/checks.md#composition-and-schema),
  [frontend](../../../docs/checks.md#frontend-packages-and-addon-fragments), [browser](../../../docs/checks.md#browser-verification).
- Loop: `.agents/skills/slice/SKILL.md`. False greens: typecheck and vitest never prove the page works;
  `@angee/gql` is stale until codegen reruns ([web build pitfalls](../angee-framework/references/web-pitfalls.md), "Generated documents are an explicit prerequisite");
  an invalid `forms:` value throws only when `RegisteredFormView` renders (registered-form.ts:44,53).

## Pitfalls

- A to-one FK the node projects as bare `ID` is a scalar leaf (`select` widget, `relationModelLabel` kept): select it without a sub-selection ([form pitfalls](references/pitfalls.md), "An `ID`-scalar to-one").
- Enums read the member name and write the lowercase value; `canonicalOptionValue` reconciles, and a status verb is `<Action set>` ([form pitfalls](references/pitfalls.md), "An enum field reads UPPERCASE").
- An M2M is `many2many` chips writing an id array, never `tagInput` ([frontend rules](../../../docs/frontend/guidelines.md#rules), "A to-many relation").
- A `FormView` create dialog under the console layout needs `<ControlBandProvider host={undefined}>` ([form pitfalls](references/pitfalls.md), "A FormView create dialog").
- A `defaultValue`/`createDefaults` seed submits on create even when `readOnly`; use `createOnly` for visible-but-fixed ([form save contracts](../../../docs/frontend/guidelines.md#form-save-contracts), "A `createDefaults` seed").
- A form in `forms:` is a component rendered by `RegisteredFormView`, so `use<Addon>T()` and `useContainer` work inside it (registered-form.ts:50; `TagForm.tsx:13-15`; [frontend rules](../../../docs/frontend/guidelines.md#rules), "Route every user-facing string").

## Read next

- [Frontend rules](../../../docs/frontend/guidelines.md#rules), [Form save contracts](../../../docs/frontend/guidelines.md#form-save-contracts), [Containers](../../../docs/frontend/guidelines.md#containers)
- [Form pitfalls](references/pitfalls.md): widget, save and dialog traps moved from the frontend guide
- [Backend rules](../../../docs/backend/guidelines.md#rules), [Persistence primitives](../../../docs/backend/guidelines.md#persistence-primitives), [Write contracts](../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts)
- [Stack: backend](../../../docs/stack.md#backend) (pydantic, jsonschema, strawberry-django-hasura, money), [Stack: frontend](../../../docs/stack.md#frontend) (ajv, react-hook-form, valibot)
- Skills: `angee-addon`, `angee-models`, `angee-graphql`, `angee-pages`, `slice`

## Sources

<!--
Docs: AGENTS.md; docs/guidelines.md; docs/frontend/guidelines.md #package-layering, #ownership-boundaries, #one-way-rules, #native-state-owners, #reserved-translation-namespace, #component-ontology, #rules, #authoring, #narrowing-and-when, #variants-and-impl, #framework-containers, #context-aside, #resource-views, #rendering-and-testing, #form-save-contracts; docs/backend/guidelines.md #rules, #migration-history, #resource-loading, #naming, #checks; docs/stack.md #backend, #stack-serve-mode, #frontend; docs/checks.md #agent-methodology-and-documentation, #composition-and-schema, #frontend-packages-and-addon-fragments; docs/glossary.md (no form/widget terms).
Code: src/angee/base/schemas/{forms.py,form_spec.schema.json}; base/implementations.py:28,46-47,95-160; base/exceptions.py:25-60; graphql/schema.py:150-235; graphql/data/resource_fields.py:40-175; money/{pyproject.toml,fields.py,web/**}; tags/web/src/**; tags/models.py:74-85; workflows/web/src/run-workflow-actions.tsx; workflows_agents/steps.py:29,38; examples/src/example/notes/web/src/NotePage.tsx; packages/ui/src/views/form/{registered-form.ts,FormView.tsx:1-140,Form.tsx,form-spec-schema.ts,form-spec.ts:60-140,json-schema.ts:1-60,117-198,validation-errors.ts:1-100,191-231,use-form-view-save.ts:1-60,143,187-220,486-490}; views/page/{Field.tsx:18-115,Group.tsx:9-67}; views/resource/model-metadata-defaults.ts:61,114; views/relation/RelationRecordDialog.tsx:20,39,253-259; widgets/types.ts:110-212; runtime/{runtime.ts:362-372,contracts.ts:156-161}; packages/metadata/src/{index.ts,artifact-schema.ts:1-80,fields.ts:62-75}; packages/refine/src/operations.ts:18,63,292,320; packages/app/src/define-addon.ts:157,164; packages/ui/package.json:780-781; tests/test_impl.py:669-800.
Outstanding discrepancies and gaps: `fractionDigitsField` (packages/ui/src/widgets/types.ts:151) is read by src/angee/money/web/src/widgets/money.tsx:94, but no backend emits it and packages/metadata/src/artifact-schema.ts has no key: an authored <Field> prop documented nowhere. docs/glossary.md has no FormSpec or widget term.
-->
