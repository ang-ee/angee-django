import { Button } from "@angee/ui";
import type { ReactNode } from "react";

/** A presentation-only operator daemon action used outside collection columns. */
export interface OperatorAction<TSubject> {
  label: string;
  variant: "secondary" | "ghost";
  perform: (subject: TSubject) => void;
}

export interface OperatorActionButtonsProps<TSubject> {
  actions: readonly OperatorAction<TSubject>[];
  busy: boolean;
  subject: TSubject;
  className?: string;
}

/** Compact operator controls for detail headers and embedded daemon rows. */
export function OperatorActionButtons<TSubject>({
  actions,
  busy,
  subject,
  className = "flex justify-end gap-1",
}: OperatorActionButtonsProps<TSubject>): ReactNode {
  return (
    <div className={className}>
      {actions.map((action) => (
        <Button
          key={action.label}
          disabled={busy}
          onClick={() => action.perform(subject)}
          size="sm"
          variant={action.variant}
        >
          {action.label}
        </Button>
      ))}
    </div>
  );
}
