import { useState, type ReactElement, type ReactNode } from "react";
import { useNavigate } from "@tanstack/react-router";
import { rowPublicId } from "@angee/metadata";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { ControlBandProvider } from "../layouts/ControlBand";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { TextLink } from "../ui/text-link";
import {
  RelationField,
  type RelationOption,
} from "../widgets/RelationField";
import { FormView } from "./FormView";
import type { FieldDescriptor } from "./page";

/** What the inline create form needs to make a new related record. */
export interface RelationCreateConfig {
  /** Related model label, e.g. `"Drive"`. */
  resource: string;
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

/** What the inline edit form needs to edit the *selected* related record. */
export interface RelationEditConfig {
  /** Related model label, e.g. `"OAuthClient"`. */
  resource: string;
  /** Fields the inline edit form renders (the related model's editable fields). */
  fields?: readonly FieldDescriptor[];
  /** Dialog title; defaults to `Edit <model>`. */
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
  /**
   * Enables the in-place "Edit" affordance: a pencil beside the picker opens the
   * *selected* record in a form dialog, so a related record is changed without
   * leaving the parent surface. Server-enforced permission surfaces in the form.
   */
  edit?: RelationEditConfig;
  /** Called with the id after an inline edit (e.g. to refetch options for a relabel). */
  onEdited?: (id: string) => void;
  /**
   * Notified when the picker *popover* opens or closes — distinct from this
   * component's create/edit *dialog* state. Lets the caller defer the option
   * fetch until first open.
   */
  onOpenChange?: (open: boolean) => void;
  /**
   * In-app path to the selected record's detail page. When set, a "follow" arrow
   * beside the picker navigates there — so a chosen relation is a link to its
   * record, not a dead end. Omitted when the target model has no routed page.
   */
  followHref?: string;
}

/** The open inline-form dialog: a create prefilled with the typed query, or an edit of a record. */
type DialogState =
  | { mode: "create"; query: string }
  | { mode: "edit"; id: string };

/**
 * A `RelationField` backed by inline create/edit forms and a "follow" arrow. The
 * caller supplies the options (and, to enable an affordance, the related model +
 * its form fields); "Create …" opens a create dialog prefilled with the typed
 * name, the pencil edits the selected record, and the arrow opens its detail page
 * — all without leaving the parent surface.
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
  edit,
  onEdited,
  onOpenChange,
  followHref,
}: RelationPickerProps): ReactElement {
  const t = useUiT();
  // The open inline-form dialog; `null` means closed.
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const prefillField = create?.prefillField ?? "name";
  const canEdit = Boolean(edit) && !readOnly && Boolean(value);

  return (
    <>
      <div className="flex min-w-0 items-center gap-1">
        <div className="min-w-0 flex-1">
          <RelationField
            value={value}
            onChange={onChange}
            options={options}
            placeholder={placeholder}
            searchPlaceholder={searchPlaceholder}
            aria-label={ariaLabel}
            readOnly={readOnly}
            onCreate={create ? (query) => setDialog({ mode: "create", query }) : undefined}
            onOpenChange={onOpenChange}
          />
        </div>
        {canEdit && value ? (
          <Button
            type="button"
            variant="ghost"
            size="iconMd"
            aria-label={t("relation.edit")}
            className="shrink-0"
            onClick={() => setDialog({ mode: "edit", id: value })}
          >
            <Glyph decorative name="pencil" />
          </Button>
        ) : null}
        {followHref ? <FollowRecordLink href={followHref} /> : null}
      </div>
      <Dialog.Root
        open={dialog !== null}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Backdrop />
          <Dialog.Content size="lg">
            <Dialog.Header>
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <Dialog.Title>{dialogTitle(dialog, create, edit)}</Dialog.Title>
                </div>
                <Dialog.Close />
              </div>
            </Dialog.Header>
            <Dialog.Body>
              {/* Force the form's control band inline so Save lands in the dialog
                  instead of portaling to the layout's top band. */}
              {dialog?.mode === "create" && create ? (
                <ControlBandProvider host={undefined}>
                  <FormView
                    resource={create.resource}
                    id={null}
                    fields={create.fields}
                    defaultValues={{ [prefillField]: dialog.query }}
                    onSaved={(row) => {
                      const id = rowPublicId(row);
                      if (id) {
                        onChange?.(id);
                        onCreated?.(id);
                      }
                      setDialog(null);
                    }}
                  />
                </ControlBandProvider>
              ) : null}
              {dialog?.mode === "edit" && edit ? (
                <ControlBandProvider host={undefined}>
                  <FormView
                    resource={edit.resource}
                    id={dialog.id}
                    fields={edit.fields}
                    onSaved={(row) => {
                      onEdited?.(rowPublicId(row) || dialog.id);
                      setDialog(null);
                    }}
                  />
                </ControlBandProvider>
              ) : null}
            </Dialog.Body>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}

function dialogTitle(
  dialog: DialogState | null,
  create: RelationCreateConfig | undefined,
  edit: RelationEditConfig | undefined,
): ReactNode {
  if (dialog?.mode === "edit") {
    return edit?.title ?? `Edit ${edit?.resource.toLowerCase() ?? "record"}`;
  }
  return create?.title ?? `New ${create?.resource.toLowerCase() ?? "record"}`;
}

/**
 * The "follow" arrow beside a relation picker: a client-side navigation to the
 * selected record's detail page. A separate component so its router hook runs
 * only when a follow target exists (router-less renders pass no `followHref`).
 */
function FollowRecordLink({ href }: { href: string }): ReactElement {
  const t = useUiT();
  const navigate = useNavigate();
  return (
    // A real `<a href>` (via `TextLink`) so cmd/middle-click opens the record in
    // a new tab and the control reads as a link to AT; a plain click does SPA
    // navigation through `onNavigate`.
    <TextLink
      href={href}
      onNavigate={(to) => void navigate({ to })}
      aria-label={t("relation.follow")}
      variant="muted"
      className="inline-flex size-icon-btn-md shrink-0 items-center justify-center rounded-6 transition-colors hover:bg-inset focus-visible:focus-ring [&_.glyph]:size-4"
    >
      <Glyph decorative name="arrow-up-right" />
    </TextLink>
  );
}
