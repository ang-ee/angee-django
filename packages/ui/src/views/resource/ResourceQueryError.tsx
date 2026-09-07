import { ErrorBanner } from "../../fragments/ErrorBanner";
import { Button } from "../../ui/button";
import { useUiT } from "../../i18n";

/** Invalid query state remains visible and blocks reads until explicitly reset. */
export function ResourceQueryError({ error, onReset }: { error: Error; onReset: () => void }) {
  const t = useUiT();
  return <ErrorBanner description={error.message} actions={
    <Button variant="secondary" onClick={onReset}>{t("query.reset")}</Button>
  } />;
}
