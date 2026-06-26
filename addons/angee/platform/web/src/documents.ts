// Hand-authored console query against the platform introspection surface. The
// platform backend owns the schema (`addons/angee/platform/schema.py`); this
// document mirrors it and the result types are derived from it, the same
// no-codegen pattern IAM uses. The resource ledger listing is owned by the
// `resources` addon, not here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const PlatformExplorer = graphql(`
  query PlatformExplorer {
    platform_explorer {
      addons {
        id
        label
        namespace
        kind
        model_count
        field_count
        resource_count
        depends_on
        model_labels
      }
      models {
        label
        app_label
        model_name
        verbose_name
        db_table
        addon_id
        addon_label
        resource_type
        field_count
        relation_count
        depends_on
        fields {
          name
          attname
          kind
          is_relation
          relation_target
          addon
        }
      }
      edges {
        id
        source
        target
        kind
        field_name
      }
    }
  }
`);

/** The `platform_explorer` payload; `null` when the surface is unavailable. */
export type PlatformExplorerData = NonNullable<
  DocumentType<typeof PlatformExplorer>["platform_explorer"]
>;

export type PlatformAddonData = PlatformExplorerData["addons"][number];
export type PlatformModelData = PlatformExplorerData["models"][number];
export type PlatformEdgeData = PlatformExplorerData["edges"][number];
export type PlatformFieldData = PlatformModelData["fields"][number];
