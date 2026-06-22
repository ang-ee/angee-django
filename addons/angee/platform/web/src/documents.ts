// Hand-authored console query against the platform introspection surface. The
// platform backend owns the schema (`addons/angee/platform/schema.py`); this
// document mirrors it and the result types are derived from it, the same
// no-codegen pattern IAM uses. The resource ledger listing is owned by the
// `resources` addon, not here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const PlatformExplorer = graphql(`
  query PlatformExplorer {
    platformExplorer {
      addons {
        id
        label
        namespace
        kind
        modelCount
        fieldCount
        resourceCount
        dependsOn
        modelLabels
      }
      models {
        label
        appLabel
        modelName
        verboseName
        dbTable
        addonId
        addonLabel
        resourceType
        fieldCount
        relationCount
        dependsOn
        fields {
          name
          attname
          kind
          isRelation
          relationTarget
          addon
        }
      }
      edges {
        id
        source
        target
        kind
        fieldName
      }
    }
  }
`);

/** The `platformExplorer` payload; `null` when the surface is unavailable. */
export type PlatformExplorerData = NonNullable<
  DocumentType<typeof PlatformExplorer>["platformExplorer"]
>;

export type PlatformAddonData = PlatformExplorerData["addons"][number];
export type PlatformModelData = PlatformExplorerData["models"][number];
export type PlatformEdgeData = PlatformExplorerData["edges"][number];
export type PlatformFieldData = PlatformModelData["fields"][number];
