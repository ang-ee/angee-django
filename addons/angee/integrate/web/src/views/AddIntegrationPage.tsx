import { useAuthoredQuery } from "@angee/refine";
import { rowPublicId } from "@angee/metadata";
import {
  Button,
  EmptyState,
  ErrorBanner,
  errorMessage,
  Glyph,
  LoadingPanel,
  RegisteredFormView,
  useResourceRecordHrefLookup,
  useResourceRoute,
  useRouteHref,
  useRouteSearch,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import {
  IntegrationCapabilities,
} from "../documents";
import { useIntegrateT } from "../i18n";

/** Capability-first entry point for creating a concrete integration child. */
export function AddIntegrationPage(): React.ReactElement {
  const t = useIntegrateT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const search = useRouteSearch();
  const recordHref = useResourceRecordHrefLookup();
  const query = useAuthoredQuery(IntegrationCapabilities);
  const selectedResource = typeof search.capability === "string" ? search.capability : "";
  const capabilities = query.data?.integration_capabilities ?? [];
  const selected = capabilities.find((capability) => capability.resource === selectedResource) ?? null;
  const connectRoute = useResourceRoute(selected?.resource ?? "");

  if (query.isLoading) return <LoadingPanel message={t("integrations.add.loading")} />;
  if (query.error) return <ErrorBanner title={t("integrations.add.loadError")} description={errorMessage(query.error, t("integrations.add.loadError"))} />;
  const selectCapability = (resource?: string): void => {
    const base = routeHref("integrate.add");
    void navigate({ to: resource ? `${base}?capability=${encodeURIComponent(resource)}` : base });
  };

  if (!selected) {
    return (
      <EmptyState
        fill
        icon="integrate"
        title={t("integrations.add.title")}
        description={t("integrations.add.description")}
        actions={capabilities.length > 0 ? capabilities.map((capability) => (
          <Button key={capability.resource} onClick={() => selectCapability(capability.resource)}>
            {capability.icon ? <Glyph decorative name={capability.icon} /> : null}
            {capability.label}
          </Button>
        )) : <span>{t("integrations.add.none")}</span>}
      />
    );
  }

  if (selected.create_mode === "CONNECT") {
    return (
      <EmptyState
        fill
        icon={selected.icon ?? "integrate"}
        title={selected.label}
        description={t("integrations.add.connectDescription")}
        actions={(
          <>
            <Button onClick={() => selectCapability()}>{t("integrations.add.back")}</Button>
            <Button
              variant="primary"
              disabled={!connectRoute}
              onClick={() => { if (connectRoute) void navigate({ to: connectRoute }); }}
            >
              {t("integrations.add.continue")}
            </Button>
          </>
        )}
      />
    );
  }

  return (
    <RegisteredFormView
      resource={selected.resource}
      id={null}
      toolbarStart={<Button onClick={() => selectCapability()}>{t("integrations.add.back")}</Button>}
      onSaved={(row) => {
        const id = rowPublicId(row);
        const href = id == null ? undefined : recordHref(selected.resource, id);
        if (href) void navigate({ to: href });
      }}
    />
  );
}
