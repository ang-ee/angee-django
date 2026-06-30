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

// Marketplace board mutations. The platform backend owns the install source
// (`settings.yaml` INSTALLED_APPS) through the AddonInstaller; the VCS marketplace
// tier (`platform_integrate_vcs`) contributes `add_source`/`scan` onto the same
// console schema. The board consumes them all through the one generated console
// contract — never an ad-hoc cross-addon import.

/** Add an addon root to `settings.yaml`; the row reflects `pending` until the next boot. */
export const InstallAddon = graphql(`
  mutation InstallAddon($addon: String!) {
    install(addon: $addon) {
      ok
      message
    }
  }
`);

/** Remove an addon root from `settings.yaml`; refused server-side for a forced addon. */
export const UninstallAddon = graphql(`
  mutation UninstallAddon($addon: String!) {
    uninstall(addon: $addon) {
      ok
      message
    }
  }
`);

/** Inventory a repository on a VCS bridge and point a new addon `Source` at it. */
export const AddAddonSource = graphql(`
  mutation AddAddonSource($data: AddonSourceInput!) {
    add_source(data: $data) {
      ok
      message
    }
  }
`);

/** Re-enumerate one addon `Source` into the marketplace (`platform.Addon`) rows. */
export const ScanAddonSource = graphql(`
  mutation ScanAddonSource($sourceId: ID!) {
    scan(source_id: $sourceId) {
      ok
      message
    }
  }
`);

/** The addon `Source` rows the Scan dialog offers — VCS sources of `kind: "addon"`. */
export const AddonSources = graphql(`
  query AddonSources {
    sources(where: { kind: { _eq: "addon" } }, order_by: [{ updated_at: desc }]) {
      id
      display_name
      kind
      ref
      path
    }
  }
`);

export type AddonSourceRow = DocumentType<typeof AddonSources>["sources"][number];
