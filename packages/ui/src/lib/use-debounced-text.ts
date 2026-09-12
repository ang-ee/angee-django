import { useEffect, useState } from "react";
import { useDebouncedCallback } from "use-debounce";

/** Input drafts stay local; debounced commits belong to the external query owner. */
export function useDebouncedText(value: string, onCommit?: (value: string) => void, delay = 300) {
  const [draft, setDraft] = useState(value);
  const commit = useDebouncedCallback((next: string) => {
    if (next !== value) onCommit?.(next);
  }, delay);
  useEffect(() => { commit.cancel(); setDraft(value); }, [value, commit]);
  useEffect(() => () => commit.cancel(), [commit]);
  return { draft, setDraft, commit };
}
