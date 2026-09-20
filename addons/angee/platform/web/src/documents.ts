// Hand-authored console query against the platform introspection surface. The
// platform backend owns the schema (`addons/angee/platform/schema.py`); this
// document mirrors it and the result types are derived from it, the same
// generated-document pattern IAM uses. The resource ledger listing is owned by the
// `resources` addon, not here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const PlatformExplorer = graphql(`
  query PlatformExplorer {
    platform_explorer {
      pending_addon_changes
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

/** Whether desired addon settings differ from the currently loaded graph. */
export const PendingAddonChanges = graphql(`
  query PendingAddonChanges {
    platform_explorer {
      pending_addon_changes
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

/** Full inspection is requested only after opening one registered implementation. */
export const PlatformImplementation = graphql(`
  query PlatformImplementation($id: String!) {
    platform_implementation(id: $id) {
      id
      model
      field
      key
      label
      category
      icon
      registry_setting
      class_path
      base_class_path
      addon_id
      addon_label
      description
      defaults
      config_schema
      source
      source_file
      source_start_line
      source_unavailable_reason
    }
  }
`);

export type PlatformImplementationData = NonNullable<
  DocumentType<typeof PlatformImplementation>["platform_implementation"]
>;

// Marketplace board mutations. The platform backend owns the install source
// (`settings.yaml` INSTALLED_APPS) through the AddonInstaller; the VCS marketplace
// tier (`platform_integrate_vcs`) contributes `add_source`/`scan` onto the same
// console schema. The board consumes them all through the one generated console
// contract — never an ad-hoc cross-addon import.

// Every marketplace verb changes the reflected board rows; keep that resource
// blast radius beside the verbs that own it.
export const PLATFORM_ADDON_MUTATION_INVALIDATES = ["platform.Addon"] as const;

/** Forecast the exact desired addon graph and loaded data touched by one change. */
export const AddonChangePreview = graphql(`
  query AddonChangePreview($addon: String!, $action: AddonChangeAction!) {
    addon_change_preview(addon: $addon, action: $action) {
      action
      addon
      revision
      can_apply
      refusal
      roots_before
      roots_after
      addons_to_enable {
        name
        label
        root
        depends_on
      }
      addons_to_disable {
        name
        label
        root
        depends_on
      }
      data_inventory {
        addon
        models {
          label
          verbose_name
          row_count
        }
        contributed_fields {
          model_label
          field_name
          verbose_name
        }
      }
      migration_warning
    }
  }
`);

/** Server-owned impact projection returned for one revision-bound addon change. */
export type AddonChangePreviewData = DocumentType<typeof AddonChangePreview>["addon_change_preview"];

/** Add an addon root to `settings.yaml`; the row reflects `pending` until the next boot. */
export const InstallAddon = graphql(`
  mutation InstallAddon($addon: String!, $revision: String) {
    install(addon: $addon, revision: $revision) {
      ok
      message
    }
  }
`);

/** Disable an addon root after a revision-bound impact preview. */
export const DisableAddon = graphql(`
  mutation DisableAddon($addon: String!, $revision: String) {
    disable(addon: $addon, revision: $revision) {
      ok
      message
    }
  }
`);
