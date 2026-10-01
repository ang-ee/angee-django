import { ResourceQuery, useModelMetadata, type QueryFilter, type FilterValue } from "@angee/metadata";
import { useUiT } from "../i18n";
import { Button } from "../ui/button";
import { buildFilterFields } from "../views/resource/utils/filter-options";
import { customFilterChipsFor, addCustomFilter } from "../views/resource/utils/filter-mutations";
import { FilterClauseEditor, type FilterClauseField } from "./FilterClauseEditor";

export interface ResourceConditionEditorProps {
  resource: string;
  value: unknown;
  onChange?: (value: Record<string, unknown>) => void;
  readOnly?: boolean;
}

/** Edit persisted Hasura conditions using resource-owned capabilities and wire rules. */
export function ResourceConditionEditor({ resource, value, onChange, readOnly = false }: ResourceConditionEditorProps) {
  const metadata = useModelMetadata(resource);
  const t = useUiT();
  if (!metadata?.resource) return <p role="status">{t("condition.selectResource")}</p>;
  const query = ResourceQuery.from(metadata);
  let filter: QueryFilter;
  try { filter = query.fromWhere(value ?? {}); } catch (error) {
    return <div className="grid gap-2">
      <p role="alert">{error instanceof Error ? error.message : t("condition.invalid")}</p>
      {!readOnly && onChange ? <Button type="button" size="sm" variant="secondary"
        onClick={() => onChange({})}>{t("condition.clear")}</Button> : null}
    </div>;
  }
  const fields = buildFilterFields([], [], metadata, query);
  return <ConditionGroup value={filter} fields={fields} readOnly={readOnly || !onChange}
    onChange={(next) => onChange?.(query.toWhere(next))} />;
}

function ConditionGroup({ value, fields, onChange, readOnly }: {
  value: QueryFilter;
  fields: readonly FilterClauseField[];
  onChange: (value: QueryFilter) => void;
  readOnly: boolean;
}) {
  const t = useUiT();
  const entries = Object.entries(value);
  const replace = (name: string, replacement?: FilterValue) => {
    const next = { ...value };
    if (replacement === undefined) delete next[name]; else next[name] = replacement;
    onChange(next);
  };
  const append = (branch: QueryFilter) => onChange(entries.length === 1 && Array.isArray(value.AND)
    ? { AND: [...value.AND, branch] } : entries.length ? { AND: [value, branch] } : branch);
  return <div className="grid gap-2 rounded-6 border border-border-subtle p-2">
    {!entries.length ? <p className="text-13 text-fg-muted">{t("condition.matchAll")}</p> : null}
    {entries.map(([name, operand]) => name === "AND" || name === "OR" ? <section key={name} className="grid gap-2">
      <div className="flex items-center gap-2"><strong>{t(name === "AND" ? "condition.all" : "condition.any")}</strong>
        {!readOnly ? <Button type="button" size="sm" variant="ghost" onClick={() => replace(name)}>{t("condition.removeGroup")}</Button> : null}
      </div>
      {(operand as readonly QueryFilter[]).map((branch, index, branches) => <div key={`${index}:${JSON.stringify(branch)}`} className="grid gap-1 pl-2">
        <ConditionGroup value={branch} fields={fields} readOnly={readOnly}
          onChange={(next) => replace(name, branches.map((current, at) => at === index ? next : current))} />
        {!readOnly ? <Button type="button" size="sm" variant="ghost"
          onClick={() => replace(name, branches.filter((_, at) => at !== index))}>{t("condition.removeBranch")}</Button> : null}
      </div>)}
      {!readOnly ? <Button type="button" size="sm" variant="secondary"
        onClick={() => replace(name, [...operand as readonly QueryFilter[], {}])}>{t("condition.addBranch")}</Button> : null}
    </section> : name === "NOT" ? <section key={name} className="grid gap-2">
      <div className="flex items-center gap-2"><strong>{t("condition.not")}</strong>
        {!readOnly ? <Button type="button" size="sm" variant="ghost" onClick={() => replace(name)}>{t("condition.removeGroup")}</Button> : null}
      </div>
      <ConditionGroup value={operand as QueryFilter} fields={fields} onChange={(next) => replace(name, next)} readOnly={readOnly} />
    </section> : <div key={name} className="grid gap-1">
      {customFilterChipsFor({ [name]: operand }, [], fields, null).map((chip) => <div key={chip.id} className="flex items-center gap-2">
        <span className="text-13">{chip.label}</span>
      </div>)}
      {!readOnly ? <Button type="button" size="sm" variant="ghost" onClick={() => replace(name)}>{t("condition.removeRule")}</Button> : null}
    </div>)}
    {!readOnly ? <>
      <FilterClauseEditor fields={fields} onSubmit={(clause) => append(addCustomFilter({}, clause))} />
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="secondary" onClick={() => append({ OR: [{}] })}>{t("condition.addAny")}</Button>
        <Button type="button" size="sm" variant="secondary" onClick={() => append({ NOT: {} })}>{t("condition.addNot")}</Button>
      </div>
    </> : null}
  </div>;
}
