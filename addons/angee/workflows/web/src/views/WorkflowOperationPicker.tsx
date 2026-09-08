import * as React from "react";
import {
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandLoading,
  CommandRoot,
  CommandSearch,
  DialogBackdrop,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  ErrorBanner,
  Glyph,
  type DialogContentProps,
} from "@angee/ui";

import { useWorkflowsT } from "../i18n";

export interface WorkflowOperationChoice {
  key: string;
  label: string;
  category: string;
  description: string;
  selectable: boolean;
  effect: string;
  effect_description: string;
}

export interface WorkflowOperationPickerProps {
  operations: readonly WorkflowOperationChoice[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChoose: (key: string) => void;
  loading?: boolean;
  error?: string | null;
  finalFocus?: DialogContentProps["finalFocus"];
}

/** Search and choose one selectable operation from server-declared metadata. */
export function WorkflowOperationPicker({
  operations,
  open,
  onOpenChange,
  onChoose,
  loading = false,
  error = null,
  finalFocus,
}: WorkflowOperationPickerProps): React.ReactElement {
  const t = useWorkflowsT();
  const groups = React.useMemo(() => groupOperations(operations), [operations]);

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent size="md" finalFocus={finalFocus}>
          <DialogHeader>
            <DialogTitle>{t("palette.title")}</DialogTitle>
            <DialogDescription>{t("palette.description")}</DialogDescription>
          </DialogHeader>
          <CommandRoot label={t("palette.title")}>
            <CommandSearch>
              <CommandInput autoFocus placeholder={t("palette.search")} />
            </CommandSearch>
            <CommandList>
              {loading ? <CommandLoading>{t("palette.loading")}</CommandLoading> : null}
              {!loading && error ? <ErrorBanner title={t("palette.error")} description={error} /> : null}
              {!loading && !error ? <CommandEmpty>{t("palette.empty")}</CommandEmpty> : null}
              {!loading && !error
                ? groups.map(([category, choices]) => (
                    <CommandGroup key={category} heading={category}>
                      {choices.map((operation) => (
                        <CommandItem
                          key={operation.key}
                          value={operationSearchValue(operation)}
                          className="h-auto min-h-12 items-start gap-3 py-2"
                          onSelect={() => {
                            onChoose(operation.key);
                            onOpenChange(false);
                          }}
                        >
                          <Glyph decorative name="workflow-run" className="mt-0.5 shrink-0" />
                          <span className="min-w-0 flex-1">
                            <span className="block text-13 font-medium text-fg">{operation.label}</span>
                            <span className="block text-xs text-fg-muted">{operation.description}</span>
                            <span className="mt-0.5 block text-2xs text-fg-muted">
                              {t("palette.effect", { effect: effectDescription(operation, t) })}
                            </span>
                          </span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  ))
                : null}
            </CommandList>
          </CommandRoot>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}

function groupOperations(
  operations: readonly WorkflowOperationChoice[],
): readonly [string, readonly WorkflowOperationChoice[]][] {
  const groups = new Map<string, WorkflowOperationChoice[]>();
  for (const operation of operations) {
    if (!operation.selectable) continue;
    const group = groups.get(operation.category) ?? [];
    group.push(operation);
    groups.set(operation.category, group);
  }
  return [...groups];
}

function operationSearchValue(operation: WorkflowOperationChoice): string {
  return [
    operation.key,
    operation.label,
    operation.category,
    operation.description,
    operation.effect_description,
  ]
    .filter(Boolean)
    .join(" ");
}

function effectDescription(
  operation: WorkflowOperationChoice,
  t: ReturnType<typeof useWorkflowsT>,
): string {
  if (operation.effect_description.trim()) return operation.effect_description;
  const declared = operation.effect.toLowerCase();
  const key = ["none", "read", "write", "external"].includes(declared) ? declared : "unknown";
  return t(`canvas.effect.${key}`);
}
