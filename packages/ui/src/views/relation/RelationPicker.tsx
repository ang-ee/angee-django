import { useState, type ReactElement, type ReactNode, type Ref } from "react";

import { Glyph } from "../../chrome/Glyph";
import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";
import { TextLink } from "../../ui/text-link";
import {
  RelationField,
  type RelationSearchState,
  type RelationOption,
  type RelationFieldProps,
} from "../../widgets/RelationField";
import type { FormSubmit } from "../form/FormView";
import type { FieldDescriptor } from "../page";
import { RelationRecordDialog, type RelationDialogState } from "./RelationRecordDialog";

/** What the inline create form needs to make a new related record. */
export interface RelationCreateConfig {
  /** Related model label, e.g. `"storage.Drive"`. */
  resource: string;
  /**
   * Fields the inline create form renders. Optional: when the model registers a
   * create form via `defineAddon`'s `forms:`, `FormView` resolves it by model
   * name and these are unused — pass them only when the form is data-dependent
   * (e.g. runtime-fetched options) and cannot be a static registration.
   */
  fields?: readonly FieldDescriptor[];
  /**
   * Custom save owner for a related model that exposes no stock create root (see
   * `FormView.submit`) — a model whose create carries material its node type
   * deliberately never projects, so auto-CRUD cannot express it. The inline
   * create form saves through this instead, and the returned row is selected.
   */
  submit?: FormSubmit;
  /** Pure-data seeds supplied by the owning surface (for example a scoped parent id). */
  defaultValues?: Readonly<Record<string, unknown>>;
  /** Field prefilled with the typed query — the new record's name (default `"name"`). */
  prefillField?: string;
  /** Dialog title; defaults to `New <model>`. */
  title?: ReactNode;
  /** Optional always-visible action that opens the same native create form. */
  actionLabel?: ReactNode;
}

/** What the inline edit form needs to edit the *selected* related record. */
export interface RelationEditConfig {
  /** Related model label, e.g. `"integrate.OAuthClient"`. */
  resource: string;
  /** Fields the inline edit form renders (the related model's editable fields). */
  fields?: readonly FieldDescriptor[];
  /** Dialog title; defaults to `Edit <model>`. */
  title?: ReactNode;
}

export interface RelationPickerProps extends Pick<RelationFieldProps, "presentation"> {
  controlRef?: Ref<HTMLButtonElement>;
  id?: string;
  value?: string | null;
  onChange?: (value: string) => void;
  onCommit?: () => void;
  options: readonly RelationOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  "aria-required"?: boolean;
  "aria-invalid"?: boolean;
  readOnly?: boolean;
  /**
   * Enables native in-place creation. A no-match typed query always offers the
   * "Create …" row; `actionLabel` additionally exposes the same form as a visible
   * button. The saved record is selected, and denied creates surface through the
   * form's own server-backed error banner.
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
  onSearchChange?: (query: string) => void;
  searchState?: RelationSearchState;
  /**
   * In-app path to the selected record's detail page. When set, a "follow" arrow
   * beside the picker navigates there — so a chosen relation is a link to its
   * record, not a dead end. Omitted when the target model has no routed page.
   */
  followHref?: string;
}

/**
 * A `RelationField` backed by inline create/edit forms and a "follow" arrow. The
 * caller supplies the options (and, to enable an affordance, the related model +
 * its form fields); "Create …" opens a create dialog prefilled with the typed
 * name, an authored create action can expose that same form directly, the pencil
 * edits the selected record, and the arrow opens its detail page — all without
 * leaving the parent surface.
 */
export function RelationPicker({
  controlRef,
  id,
  value,
  onChange,
  onCommit,
  options,
  placeholder,
  searchPlaceholder,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
  "aria-required": ariaRequired,
  "aria-invalid": ariaInvalid,
  readOnly,
  create,
  onCreated,
  edit,
  onEdited,
  onOpenChange,
  onSearchChange,
  searchState,
  followHref,
  presentation,
}: RelationPickerProps): ReactElement {
  const t = useUiT();
  // The open inline-form dialog; `null` means closed.
  const [dialog, setDialog] = useState<RelationDialogState | null>(null);
  const canCreate = Boolean(create?.actionLabel) && !readOnly;
  const canEdit = Boolean(edit) && !readOnly && Boolean(value);

  return (
    <>
      <div className="flex min-w-0 items-center gap-1">
        <div className="min-w-0 flex-1">
          <RelationField
            presentation={presentation}
            triggerRef={controlRef}
            id={id}
            value={value}
            onChange={(next) => {
              onChange?.(next);
              onCommit?.();
            }}
            options={options}
            placeholder={placeholder}
            searchPlaceholder={searchPlaceholder}
            aria-label={ariaLabel}
            aria-labelledby={ariaLabelledBy}
            aria-describedby={ariaDescribedBy}
            aria-required={ariaRequired}
            aria-invalid={ariaInvalid}
            readOnly={readOnly}
            onCreate={
              create
                ? (query) => setDialog({ mode: "create", query })
                : undefined
            }
            onOpenChange={onOpenChange}
            onSearchChange={onSearchChange}
            searchState={searchState}
          />
        </div>
        {canCreate && create ? (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="shrink-0"
            onClick={() => setDialog({ mode: "create", query: "" })}
          >
            {create.actionLabel}
          </Button>
        ) : null}
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
        {followHref ? <TextLink href={followHref} aria-label={t("relation.follow")} variant="muted"
          className="inline-flex size-icon-btn-md shrink-0 items-center justify-center rounded-6 transition-colors hover:bg-inset focus-visible:focus-ring [&_.glyph]:size-4">
          <Glyph decorative name="arrow-up-right" />
        </TextLink> : null}
      </div>
      <RelationRecordDialog
        dialog={dialog}
        create={create}
        edit={edit}
        onClose={() => setDialog(null)}
        onCreated={(id) => {
          onChange?.(id);
          onCommit?.();
          onCreated?.(id);
        }}
        onEdited={onEdited}
      />
    </>
  );
}
