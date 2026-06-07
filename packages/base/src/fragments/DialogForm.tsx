import * as React from "react";
import type { FormEventHandler } from "react";

import {
  Dialog,
  type DialogPlacement,
  type DialogSize,
} from "../ui/dialog";
import { FormRoot } from "../ui/form";
import { FormActions, FormGrid } from "../ui/form-layout";

export interface DialogFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  onSubmit?: FormEventHandler<HTMLFormElement>;
  size?: DialogSize;
  placement?: DialogPlacement;
}

export function DialogForm({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  onSubmit,
  size = "sm",
  placement = "prompt",
}: DialogFormProps): React.ReactElement {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content placement={placement} size={size}>
          <FormRoot layout="plain" onSubmit={onSubmit}>
            <Dialog.Header>
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <Dialog.Title>{title}</Dialog.Title>
                  {description ? (
                    <Dialog.Description>{description}</Dialog.Description>
                  ) : null}
                </div>
                <Dialog.Close />
              </div>
            </Dialog.Header>
            <Dialog.Body>
              <FormGrid>{children}</FormGrid>
            </Dialog.Body>
            {footer ? (
              <Dialog.Footer>
                <FormActions>{footer}</FormActions>
              </Dialog.Footer>
            ) : null}
          </FormRoot>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
