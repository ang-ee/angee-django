import * as React from "react";
import { Column, ResourceList, Facet, Field, Form, Group, List } from "@angee/ui";

import { useIntegrateT } from "../i18n";
import { AddRepositoryControl } from "./AddRepositoryControl";

const MODEL = "integrate.Repository";

const repositoryList = (
  <List resource={MODEL}>
    <Facet field="vcs_bridge" label="VCS bridge" labelField="display_name" />
    <Column field="org" />
    <Column field="name" />
    <Column field="visibility" widget="statusBadge" />
    <Column field="default_branch" />
    <Column field="web_url" />
  </List>
);

/**
 * The inventoried repositories, with the "Add repository" typeahead in the list
 * toolbar (the framework toolbar slot, not a hand-rolled control-band sibling).
 * The list creates nothing (`hideCreate`); rows arrive via the typeahead or
 * `discover` and leave via the per-record delete.
 */
export function RepositoriesPage(): React.ReactElement {
  const t = useIntegrateT();
  return (
    <ResourceList
      resource={MODEL}
      placement="inline"
      routed
      hideCreate
      toolbarActions={<AddRepositoryControl />}
    >
      {repositoryList}
      {/* Repositories are inventoried by the add typeahead and `discover`,
          never hand-created or edited, so the detail is read-only; ResourceList
          still wires the per-record delete (the only Repository mutation). */}
      <Form resource={MODEL}>
        <Field name="vcs_bridge" readOnly />
        <Group label={t("repositories.repository")} columns={2}>
          <Field name="org" readOnly />
          <Field name="name" readOnly />
          <Field name="default_branch" readOnly />
          <Field name="visibility" readOnly />
        </Group>
        <Group label={t("repositories.remote")} columns={2}>
          <Field name="remote" readOnly />
          <Field name="ssh_remote" readOnly />
        </Group>
        <Field name="web_url" readOnly />
        <Field name="archived" readOnly />
      </Form>
    </ResourceList>
  );
}
