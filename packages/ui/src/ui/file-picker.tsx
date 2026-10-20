import * as React from "react";

import { useRuntimeViewAs } from "../runtime";
import { Button, type ButtonProps } from "./button";

export interface FilePickerOptions {
  /** Receives the chosen files; never called with an empty list. */
  onFiles: (files: readonly File[]) => void;
  /** The native `accept` filter (MIME types or extensions). */
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  /** The accessible name of the hidden input. */
  label?: string;
}

export interface FilePicker {
  /** Opens the browser's file dialog; a no-op while disabled. */
  open: () => void;
  /** The hidden input: render it once anywhere in the picker's tree. */
  input: React.ReactElement;
  disabled: boolean;
}

/**
 * The one file-choosing affordance: a hidden native input opened by any control
 * (a button, a menu item, a pane label verb). It owns the input's reset after
 * each pick and the view-as gate, so callers own only the upload itself.
 * @owner FilePickerButton
 */
export function useFilePicker({ onFiles, accept, multiple = true, disabled: declaredDisabled = false, label }: FilePickerOptions): FilePicker {
  const preview = useRuntimeViewAs();
  const disabled = declaredDisabled || Boolean(preview.viewAs || preview.pending);
  const ref = React.useRef<HTMLInputElement>(null);
  // The handler is read live so the element (and any callback rendering it) stays
  // referentially stable across the caller's renders.
  const onFilesRef = React.useRef(onFiles);
  onFilesRef.current = onFiles;
  const open = React.useCallback(() => {
    if (!disabled) ref.current?.click();
  }, [disabled]);
  const input = React.useMemo(
    () => (
      <input
        ref={ref}
        type="file"
        className="hidden"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        aria-label={label}
        tabIndex={-1}
        onChange={(event) => {
          const files = event.currentTarget.files;
          if (files && files.length > 0) onFilesRef.current(Array.from(files));
          // Reset so picking the same file again fires `change`.
          event.currentTarget.value = "";
        }}
      />
    ),
    [accept, disabled, label, multiple],
  );
  return { open, input, disabled };
}

export type FilePickerButtonProps = Omit<ButtonProps, "onClick" | "disabled"> & FilePickerOptions;

/**
 * A button that opens the file dialog: the Upload verb wherever files are chosen.
 * @kind primitive
 */
export const FilePickerButton = React.forwardRef<HTMLElement, FilePickerButtonProps>(function FilePickerButton(
  { onFiles, accept, multiple, disabled, label, children, ...props },
  ref,
) {
  const picker = useFilePicker({ onFiles, accept, multiple, disabled, label });
  return (
    <>
      <Button ref={ref} type="button" disabled={picker.disabled} onClick={picker.open} {...props}>
        {children}
      </Button>
      {picker.input}
    </>
  );
});
FilePickerButton.displayName = "FilePickerButton";
