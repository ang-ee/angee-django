// Hand-authored console query against the platform introspection surface. The
// platform backend owns the schema (`addons/angee/platform/schema.py`); this
// string and its result types mirror it, the same no-codegen pattern IAM uses.
// The resource ledger listing is owned by the `resources` addon, not here.

export const PLATFORM_EXPLORER_QUERY = `
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
`;

export interface PlatformFieldData {
  name: string;
  attname: string;
  kind: string;
  isRelation: boolean;
  relationTarget: string | null;
  addon: string;
}

export interface PlatformModelData {
  label: string;
  appLabel: string;
  modelName: string;
  verboseName: string;
  dbTable: string;
  addonId: string;
  addonLabel: string;
  resourceType: string | null;
  fieldCount: number;
  relationCount: number;
  dependsOn: readonly string[];
  fields: readonly PlatformFieldData[];
}

export interface PlatformEdgeData {
  id: string;
  source: string;
  target: string;
  kind: string;
  fieldName: string;
}

export interface PlatformAddonData {
  id: string;
  label: string;
  namespace: string;
  kind: string;
  modelCount: number;
  fieldCount: number;
  resourceCount: number;
  dependsOn: readonly string[];
  modelLabels: readonly string[];
}

export interface PlatformExplorerResult {
  platformExplorer: {
    addons: readonly PlatformAddonData[];
    models: readonly PlatformModelData[];
    edges: readonly PlatformEdgeData[];
  } | null;
}
