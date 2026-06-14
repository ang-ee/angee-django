import { useState, type ReactElement, type ReactNode } from "react";

import { ControlBandProvider } from "../shell/ControlBand";
import { Dialog } from "../ui/dialog";
import {
  RelationField,
  type RelationOption,
} from "../widgets/RelationField";
import { FormView } from "./FormView";
import type { FieldDescriptor } from "./page";

/** What the inline create form needs to make a new related record. */
export interface RelationCreateConfig {
  /** Related model label, e.g. `"Drive"`. */
  model: string;
  /**
   * Fields the inline create form renders. Optional: when the model registers a
   * create form via `defineAddon`'s `forms:`, `FormView` resolves it by model
   * name and these are unused — pass them only when the form is data-dependent
   * (e.g. runtime-fetched options) and cannot be a static registration.
   */
  fields?: readonly FieldDescriptor[];
  /** Field prefilled with the typed query — the new record's name (default `"name"`). */
  prefillField?: string;
  /** Dialog title; defaults to `New <model>`. */
  title?: ReactNode;
}

export interface RelationPickerProps {
  value?: string | null;
  onChange?: (value: string) => void;
  options: readonly RelationOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  "aria-label"?: string;
  readOnly?: boolean;
  /**
   * Enables the in-place "Create …" affordance: when the typed query matches no
   * option, a "Create …" row opens this create form, and the saved record's id
   * is selected. Permission is server-enforced — a denied create surfaces in the
   * form's own error banner.
   */
  create?: RelationCreateConfig;
  /** Called with the new id after an inline create (e.g. to refetch options). */
  onCreated?: (id: string) => void;
}

/**
 * A `RelationField` backed by an inline create form. The caller supplies the
 * options (and, to enable create, the related model + its create fields); on
 * "Create …" a `FormView` create dialog opens prefilled with the typed name,
 * and saving selects the new record without leaving the parent surface.
 */
export function RelationPicker({
  value,
  onChange,
  options,
  placeholder,
  searchPlaceholder,
  "aria-label": ariaLabel,
  readOnly,
  create,
  onCreated,
}: RelationPickerProps): ReactElement {
  // The typed query while the create dialog is open; `null` means closed.
  const [draftName, setDraftName] = useState<string | null>(null);
  const prefillField = create?.prefillField ?? "name";

  return (
    <>
      <RelationField
        value={value}
        onChange={onChange}
        options={options}
        placeholder={placeholder}
        searchPlaceholder={searchPlaceholder}
        aria-label={ariaLabel}
        readOnly={readOnly}
        onCreate={create ? (query) => setDraftName(query) : undefined}
      />
      {create ? (
        <Dialog.Root
          open={draftName !== null}
          onOpenChange={(open) => {
            if (!open) setDraftName(null);
          }}
        >
          <Dialog.Portal>
            <Dialog.Backdrop />
            <Dialog.Content size="lg">
              <Dialog.Header>
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <Dialog.Title>
                      {create.title ?? `New ${create.model.toLowerCase()}`}
                    </Dialog.Title>
                  </div>
                  <Dialog.Close />
                </div>
              </Dialog.Header>
              <Dialog.Body>
                {/* Force the form's control band inline so Create lands in the
                    dialog instead of portaling to the shell's top band. */}
                {draftName !== null ? (
                  <ControlBandProvider host={undefined}>
                    <FormView
                      model={create.model}
                      id={null}
                      fields={create.fields}
                      defaultValues={{ [prefillField]: draftName }}
                      submitLabel="Create"
                      onSaved={(row) => {
                        const id =
                          typeof row.id === "string"
                            ? row.id
                            : String(row.id ?? "");
                        if (id) {
                          onChange?.(id);
                          onCreated?.(id);
                        }
                        setDraftName(null);
                      }}
                    />
                  </ControlBandProvider>
                ) : null}
              </Dialog.Body>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      ) : null}
    </>
  );
}
