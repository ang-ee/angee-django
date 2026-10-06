import { useMemo, useState, type ReactElement, type ReactNode } from "react";
import {
  modelLabelSegment,
  modelMetadataForLabel,
  rowPublicId,
  useModelMetadata,
  useSchemaFieldMetadata,
  type Row,
} from "@angee/metadata";

import { useUiT } from "../../i18n";
import { ControlBandProvider } from "../../layouts/ControlBand";
import { modelDisplayLabel } from "../../lib/labels";
import { Dialog } from "../../ui/dialog";
import { SegmentedControl } from "../../ui/toggle-group";
import { FormView, type FormViewProps } from "../form/FormView";
import { RegisteredFormView, useRegisteredForm } from "../form/registered-form";
import type { FieldDescriptor } from "../page";
import { formFieldsFromMetadata, type RelationFieldInfo } from "../resource/model-metadata-defaults";
import type { RelationCreateConfig, RelationCreateKind, RelationEditConfig } from "./RelationPicker";

/** The open inline-form dialog: a create prefilled with the typed query, or an edit of a record. */
export type RelationDialogState =
  | { mode: "create"; query: string }
  | { mode: "edit"; id: string };

/**
 * The inline forms a relation control offers for its related model: the fields
 * derived from that model's metadata, and create. An explicit `create` wins and
 * `null` offers none. Otherwise a model is created inline when it has a create
 * root and form fields, and so is each of its concrete kinds (its MTI
 * children): with any such kind, create offers the model itself first, then its
 * kinds, through the create form's kind switcher. The one rule both relation
 * widgets apply.
 */
export function useRelationForms(
  relation: RelationFieldInfo,
  create: RelationCreateConfig | null | undefined,
): { fields: readonly FieldDescriptor[]; create: RelationCreateConfig | undefined } {
  const { resource, labelField, canCreate } = relation;
  const schema = useSchemaFieldMetadata();
  const metadata = useModelMetadata(resource);
  const fields = useMemo(() => formFieldsFromMetadata(metadata), [metadata]);
  const derived = useMemo((): RelationCreateConfig | undefined => {
    const own = inlineCreate(resource, canCreate, fields, labelField);
    // A kind's new row is listed and selected through the parent, so only its create root matters.
    const kinds = (metadata?.resource.concreteKinds ?? []).flatMap((kind): RelationCreateKind[] => {
      const model = modelMetadataForLabel(schema, kind);
      const kindCreate = inlineCreate(
        kind,
        Boolean(model?.resource.roots.create),
        formFieldsFromMetadata(model),
        model?.resource.recordRepresentation ?? undefined,
      );
      return kindCreate ? [{ ...kindCreate, label: modelDisplayLabel(model, kind) }] : [];
    });
    if (kinds.length === 0) return own;
    return { resource, kinds: own ? [{ ...own, label: modelDisplayLabel(metadata, resource) }, ...kinds] : kinds };
  }, [canCreate, fields, labelField, metadata, resource, schema]);
  return { fields, create: create === undefined ? derived : create ?? undefined };
}

/** A model's metadata-derived inline create: offered with a create root and form fields. */
function inlineCreate(
  resource: string,
  creatable: boolean,
  fields: readonly FieldDescriptor[],
  prefillField: string | undefined,
): RelationCreateConfig | undefined {
  return creatable && fields.length > 0 ? { resource, fields, prefillField } : undefined;
}

/** The create dialog's title: the declared one, else `New <model>`. */
export function relationCreateTitle(
  create: RelationCreateConfig | undefined,
  t: ReturnType<typeof useUiT>,
): ReactNode {
  return create?.title ?? t("relation.createTitle", {
    model: modelLabelSegment(create?.resource ?? "").toLowerCase() || "record",
  });
}

function dialogTitle(
  dialog: RelationDialogState | null,
  create: RelationCreateConfig | undefined,
  edit: RelationEditConfig | undefined,
  t: ReturnType<typeof useUiT>,
): ReactNode {
  if (dialog?.mode === "edit") {
    return edit?.title ?? t("relation.editTitle", {
      model: modelLabelSegment(edit?.resource ?? "").toLowerCase() || "record",
    });
  }
  return relationCreateTitle(create, t);
}

/**
 * The inline create/edit form dialog behind a relation control: the related
 * model's registered form when one exists, else `FormView` over the declared
 * fields. A create offering several kinds switches between their forms. Its
 * owner keeps the open state and decides what a saved record means
 * (`RelationPicker` selects it; the multi widget appends it), so this component
 * only renders the form and reports the saved id.
 */
export function RelationRecordDialog({
  dialog,
  create,
  edit,
  onClose,
  onCreated,
  onEdited,
}: {
  dialog: RelationDialogState | null;
  create?: RelationCreateConfig;
  edit?: RelationEditConfig;
  onClose: () => void;
  /** Called with the new record's public id after an inline create. */
  onCreated?: (id: string) => void;
  /** Called with the edited record's id after an inline edit. */
  onEdited?: (id: string) => void;
}): ReactElement {
  const t = useUiT();
  return (
    <Dialog.Root
      open={dialog !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg">
          <Dialog.Header>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <Dialog.Title>{dialogTitle(dialog, create, edit, t)}</Dialog.Title>
              </div>
              <Dialog.Close />
            </div>
          </Dialog.Header>
          <Dialog.Body>
            {dialog?.mode === "create" && create ? (
              <RelationCreateForm
                create={create}
                query={dialog.query}
                onSaved={(row) => {
                  const id = rowPublicId(row);
                  if (id) onCreated?.(id);
                  onClose();
                }}
              />
            ) : null}
            {dialog?.mode === "edit" && edit ? (
              <RelationRecordForm
                resource={edit.resource}
                id={dialog.id}
                fields={edit.fields}
                onSaved={(row) => {
                  onEdited?.(rowPublicId(row) || dialog.id);
                  onClose();
                }}
              />
            ) : null}
          </Dialog.Body>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** The create body: a kind switcher when the create offers several kinds, then the chosen form. */
function RelationCreateForm({
  create,
  query,
  onSaved,
}: {
  create: RelationCreateConfig;
  query: string;
  onSaved: (row: Row) => void;
}): ReactElement {
  const t = useUiT();
  const kinds = create.kinds ?? [];
  const [chosen, setChosen] = useState(kinds[0]?.resource);
  // A create without kinds is its own single kind.
  const target = kinds.find((kind) => kind.resource === chosen) ?? kinds[0] ?? create;
  const prefillField = target.prefillField ?? "name";
  return (
    <>
      {kinds.length > 1 ? (
        <SegmentedControl
          className="mb-3"
          aria-label={t("relation.kind")}
          value={target.resource}
          onValueChange={setChosen}
          options={kinds.map((kind) => ({ value: kind.resource, label: kind.label }))}
        />
      ) : null}
      <RelationRecordForm
        // Each kind is its own form: switching mounts the chosen kind's fields and create root.
        key={target.resource}
        resource={target.resource}
        id={null}
        fields={target.fields}
        submit={target.submit}
        defaultValues={query ? { ...create.defaultValues, [prefillField]: query } : create.defaultValues}
        onSaved={onSaved}
      />
    </>
  );
}

/** One related record's form: the model's registered complete form, else `FormView` over `fields`. */
function RelationRecordForm({
  fields,
  submit,
  ...props
}: Pick<FormViewProps, "resource" | "id" | "fields" | "submit" | "defaultValues" | "onSaved">): ReactElement {
  const registeredForm = useRegisteredForm(props.resource);
  // Force the form's control band inline so Save lands in the dialog instead of
  // portaling to the layout's top band.
  return (
    <ControlBandProvider host={undefined}>
      {registeredForm
        ? <RegisteredFormView {...props} />
        : <FormView {...props} fields={fields} {...(submit ? { submit } : {})} />}
    </ControlBandProvider>
  );
}
