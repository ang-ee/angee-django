import { createElement, type ReactElement, type ReactNode } from "react";
import { modelLabelSegment, rowPublicId, type Row } from "@angee/metadata";

import { useUiT } from "../../i18n";
import { ControlBandProvider } from "../../layouts/ControlBand";
import { Dialog } from "../../ui/dialog";
import { FormView } from "../form/FormView";
import { RegisteredFormView, useRegisteredForm } from "../form/registered-form";
import type { RelationCreateConfig, RelationEditConfig } from "./RelationPicker";

/** The open inline-form dialog: a create prefilled with the typed query, or an edit of a record. */
export type RelationDialogState =
  | { mode: "create"; query: string }
  | { mode: "edit"; id: string };

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
 * fields. Its owner keeps the open state and decides what a saved record means
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
  const registeredForm = useRegisteredForm(
    (dialog?.mode === "edit" ? edit?.resource : create?.resource) ?? "",
  );
  const t = useUiT();
  const prefillField = create?.prefillField ?? "name";
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
            {/* Force the form's control band inline so Save lands in the dialog
                instead of portaling to the layout's top band. */}
            {dialog?.mode === "create" && create ? (
              <ControlBandProvider host={undefined}>
                {createElement(
                  registeredForm ? RegisteredFormView : FormView,
                  {
                    resource: create.resource,
                    id: null,
                    ...(registeredForm
                      ? {}
                      : {
                          fields: create.fields,
                          ...(create.submit ? { submit: create.submit } : {}),
                        }),
                    defaultValues: dialog.query
                      ? {
                          ...create.defaultValues,
                          [prefillField]: dialog.query,
                        }
                      : create.defaultValues,
                    onSaved: (row: Row) => {
                      const id = rowPublicId(row);
                      if (id) onCreated?.(id);
                      onClose();
                    },
                  },
                )}
              </ControlBandProvider>
            ) : null}
            {dialog?.mode === "edit" && edit ? (
              <ControlBandProvider host={undefined}>
                {createElement(
                  registeredForm ? RegisteredFormView : FormView,
                  {
                    resource: edit.resource,
                    id: dialog.id,
                    ...(registeredForm ? {} : { fields: edit.fields }),
                    onSaved: (row: Row) => {
                      onEdited?.(rowPublicId(row) || dialog.id);
                      onClose();
                    },
                  },
                )}
              </ControlBandProvider>
            ) : null}
          </Dialog.Body>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
