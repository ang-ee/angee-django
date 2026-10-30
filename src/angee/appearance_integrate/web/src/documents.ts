import { graphql } from "@angee/gql/console";
export const AnalyseAppearance = graphql(`
  query AnalyseAppearance($url: String!) {
    analyseAppearance: analyse_appearance(url: $url) {
      finalUrl: final_url
      title
      siteName: site_name
      colors
      neutralTint: neutral_tint
      fonts
      warnings
    }
  }
`);
