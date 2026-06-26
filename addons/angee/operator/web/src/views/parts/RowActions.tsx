import { Button } from "@angee/ui";
import type { ReactNode } from "react";

export interface RowAction<TSubject> {
  label: string;
  variant: "secondary" | "ghost";
  perform: (subject: TSubject) => void;
}

export function RowActions<TSubject>({
  actions,
  busy,
  subject,
  className = "flex justify-end gap-1",
}: {
  actions: readonly RowAction<TSubject>[];
  busy: boolean;
  subject: TSubject;
  className?: string;
}): ReactNode {
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
