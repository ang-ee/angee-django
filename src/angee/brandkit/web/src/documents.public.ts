// Public-schema read of the default kit's colours, for every page a theme dresses,
// signed in or not. The root field lives in the `public` runtime schema, so this file
// is globbed against it by the per-schema codegen (the filename is load-bearing).

import { graphql } from "@angee/gql/public";

export const DefaultBrandKitColors = graphql(`
  query DefaultBrandKitColors {
    default_brand_kit_colors {
      brand
      accent
      neutral
      canvas
      surface
      rail
      success
      warning
      danger
      info
    }
  }
`);
