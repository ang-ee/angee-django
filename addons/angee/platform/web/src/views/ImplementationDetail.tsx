import { useMemo, type ReactElement } from "react";

import { useAuthoredQuery } from "@angee/refine";
import {
  Badge, Code, CodeBlock, DetailSection, DetailSurface, ErrorBanner, FieldDescription, FieldLabel, FieldRoot,
  ImplementationDetails, InlineEmpty, JsonValueView, LabeledDescriptorField, Tabs,
  deserializeFormSpec, errorMessage, jsonObjectFromUnknown, useAppRuntime, useRouteHref, useRouteRecordId,
} from "@angee/ui";

import { PlatformImplementation, type PlatformImplementationData } from "../documents";
import { usePlatformT } from "../i18n";
import { TextRouteLink } from "../lib/cells";

const ignoreChange = () => {};

/** Read-only schema presentation shares the ordinary implementation form descriptors. */
function ImplementationSettings({ implementation }: { implementation: PlatformImplementationData }): ReactElement {
  const t = usePlatformT();
  const { widgets } = useAppRuntime();
  const projection = useMemo(() => {
    try {
      return { fields: implementation.config_schema == null ? [] : deserializeFormSpec(implementation.config_schema, widgets), error: null };
    } catch (error) {
      return { fields: [], error: errorMessage(error, t("implementation.invalidSchema")) };
    }
  }, [implementation.config_schema, widgets, t]);
  const defaults = jsonObjectFromUnknown(implementation.defaults);
  const config = jsonObjectFromUnknown(defaults?.config) ?? {};

  if (!defaults) return <ErrorBanner description={t("implementation.invalidDefaults")} />;

  return (
    <>
      <DetailSection title={t("implementation.settings")}>
        {projection.error ? <ErrorBanner title={t("implementation.invalidSchema")} description={projection.error} /> : null}
        {projection.fields.length ? (
          <div className="grid gap-5 md:grid-cols-2">
            {projection.fields.map((field) => {
              const hasDefault = Object.hasOwn(config, field.name) || field.hasDefault;
              const value = Object.hasOwn(config, field.name) ? config[field.name] : field.defaultValue;
              return (
                <div key={field.name} className="min-w-0 space-y-2">
                  <Badge>{field.kind}</Badge>
                  {hasDefault
                    ? <LabeledDescriptorField field={field} value={value} readOnly onChange={ignoreChange} />
                    : <FieldRoot>
                        <FieldLabel nativeLabel={false} render={<span />} required={field.required}>{field.label ?? field.name}</FieldLabel>
                        <InlineEmpty label={t("implementation.noDefault")} />
                        {field.description ? <FieldDescription>{field.description}</FieldDescription> : null}
                      </FieldRoot>}
                </div>
              );
            })}
          </div>
        ) : !projection.error ? <InlineEmpty label={t("implementation.noSettings")} /> : null}
      </DetailSection>
      {Object.keys(defaults).length ? <DetailSection title={t("implementation.defaults")}><JsonValueView value={defaults} /></DetailSection> : null}
    </>
  );
}

export function ImplementationDetail(): ReactElement {
  const t = usePlatformT();
  const id = useRouteRecordId();
  const routeHref = useRouteHref();
  const query = useAuthoredQuery(PlatformImplementation, { id: id ?? "" }, { enabled: Boolean(id) });
  const implementation = query.data?.platform_implementation;

  if (query.error) return <ErrorBanner title={t("implementation.loadError")} description={errorMessage(query.error, t("implementation.loadError"))} />;

  return (
    <DetailSurface
      publishBreadcrumbLabel
      loading={query.isPending}
      loadingMessage={t("implementation.loading")}
      empty={!implementation ? { icon: "terminal", title: t("implementation.notFound"), description: id } : null}
      title={implementation?.label}
      description={implementation?.description}
      meta={implementation ? <Code>{implementation.class_path}</Code> : null}
    >
      {implementation ? (
        <>
          <Tabs defaultValue="overview">
            <Tabs.List aria-label={t("implementation.sections")}>
              <Tabs.Tab value="overview">{t("implementation.overview")}</Tabs.Tab>
              <Tabs.Tab value="settings">{t("implementation.settings")}</Tabs.Tab>
              <Tabs.Tab value="code">{t("implementation.code")}</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="overview">
              <DetailSection title={t("detail.definition")} rows={[
                [t("implementation.key"), <Code>{implementation.key}</Code>],
                [t("col.category"), implementation.category],
                [t("col.model"), <TextRouteLink href={routeHref("platform.models.record", { id: implementation.model.toLowerCase() })}>{implementation.model}</TextRouteLink>],
                [t("col.field"), implementation.field],
                [t("implementation.registry"), <Code>{implementation.registry_setting}</Code>],
                [t("implementation.baseClass"), <Code>{implementation.base_class_path}</Code>],
                [t("col.addon"), implementation.addon_id
                  ? <TextRouteLink href={routeHref("platform.addons.record", { id: implementation.addon_id })}>{implementation.addon_label}</TextRouteLink>
                  : t("implementation.external")],
              ]} />
            </Tabs.Panel>
            <Tabs.Panel value="settings"><ImplementationSettings implementation={implementation} /></Tabs.Panel>
            <Tabs.Panel value="code">
              <DetailSection title={t("implementation.code")}>
                {implementation.source_file ? <Code>{implementation.source_file}{implementation.source_start_line ? `:${implementation.source_start_line}` : ""}</Code> : null}
                {implementation.source != null
                  ? <CodeBlock className="mt-3">{implementation.source}</CodeBlock>
                  : <InlineEmpty label={implementation.source_unavailable_reason ?? t("implementation.noSource")} />}
              </DetailSection>
            </Tabs.Panel>
          </Tabs>
          <ImplementationDetails value={{ model: implementation.model, field: implementation.field, choice: implementation }} />
        </>
      ) : null}
    </DetailSurface>
  );
}
